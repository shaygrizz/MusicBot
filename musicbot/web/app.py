"""Веб-часть для Telegram Mini App: страница-дашборд и API с проверкой подписи Telegram."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import time
import urllib.parse
from pathlib import Path
from urllib.parse import parse_qsl

from aiogram import Bot
from aiogram.types import FSInputFile
from aiohttp import ClientSession, ClientTimeout, web

from .. import audiocache, db, download, favcache, net, paths, search
from .. import favorites as favlib  # внутри make_app есть обработчик с именем favorites
from ..download import Downloaded, SearchResult

INDEX = Path(__file__).parent / "static" / "index.html"
INITDATA_MAX_AGE = 24 * 3600  # старше суток подпись не принимаем
RECS_TTL = 6 * 3600
_recs_cache: dict[int, tuple[float, list]] = {}


def validate_init_data(init_data: str, bot_token: str, max_age: int = INITDATA_MAX_AGE) -> dict:
    """Проверка подписи Telegram WebApp initData по официальной схеме:
    secret = HMAC_SHA256("WebAppData", bot_token); hash = HMAC_SHA256(secret, data_check_string)."""
    params = dict(parse_qsl(init_data, keep_blank_values=True))
    received = params.pop("hash", None)
    if not received:
        raise ValueError("нет hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received.lower()):
        raise ValueError("неверная подпись")
    try:
        age = time.time() - int(params.get("auth_date", "0"))
    except ValueError:
        raise ValueError("плохая auth_date")
    if age > max_age or age < -60:
        raise ValueError("данные устарели")
    user = json.loads(params.get("user", "null"))
    if not user or "id" not in user:
        raise ValueError("нет пользователя")
    return user


def _get_json(url: str):
    return net.get_json(url)


def _deezer_recs(artists: list[str]) -> list[dict]:
    """Рекомендации: у любимых исполнителей находим похожих и берём их главный трек."""
    out, seen = [], {a.lower() for a in artists}
    for name in artists:
        try:
            found = _get_json("https://api.deezer.com/search/artist?limit=1&q=" + urllib.parse.quote(name))
            if not found.get("data"):
                continue
            related = _get_json(f"https://api.deezer.com/artist/{found['data'][0]['id']}/related?limit=6")
            for rel in related.get("data", []):
                if rel["name"].lower() in seen:
                    continue
                seen.add(rel["name"].lower())
                top = _get_json(f"https://api.deezer.com/artist/{rel['id']}/top?limit=1").get("data", [])
                if top:
                    out.append(
                        {
                            "artist": rel["name"],
                            "title": top[0]["title"],
                            "duration": int(top[0].get("duration") or 0),
                            "cover": (top[0].get("album") or {}).get("cover_medium", ""),
                            "because": name,
                        }
                    )
        except Exception as e:
            logging.info("рекомендации для %s: %s", name, str(e)[:80])
    return out[:10]


def _deezer_chart() -> list[dict]:
    data = _get_json("https://api.deezer.com/chart/0/tracks?limit=10").get("data", [])
    return [
        {
            "artist": t["artist"]["name"],
            "title": t["title"],
            "duration": int(t.get("duration") or 0),
            "cover": (t.get("album") or {}).get("cover_medium", ""),
            "because": "",
        }
        for t in data
    ]


async def _recs(user_id: int) -> list[dict]:
    cached = _recs_cache.get(user_id)
    if cached and time.time() - cached[0] < RECS_TTL:
        return cached[1]
    artists = await db.top_artist_names(user_id, 3)
    recs = await asyncio.to_thread(_deezer_recs, artists) if artists else []
    if not recs:
        try:
            recs = await asyncio.to_thread(_deezer_chart)
        except Exception:
            recs = []
    _recs_cache[user_id] = (time.time(), recs)
    return recs


AVATAR_DIR = paths.AVATARS
ADMINS = {int(x) for x in os.getenv("ADMIN_USERS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}
AVATAR_MAX = 20 * 1024 * 1024
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,40}$")


def _clean_name(raw) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(raw or ""))
    return re.sub(r"\s+", " ", name).strip()[:30]


def _make_avatar(data: bytes, dest: Path, w: int = 256, h: int = 256, q: int = 4) -> None:
    """Любая картинка (jpeg, png, webp…) -> JPEG w x h с обрезкой по центру; метаданные отбрасываются."""
    tmp = dest.with_suffix(".in")
    tmp.write_bytes(data)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(tmp),
                "-vf",
                f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=yuvj420p",
                "-frames:v",
                "1",
                "-q:v",
                str(q),
                "-map_metadata",
                "-1",
                str(dest),
            ],
            check=True,
            timeout=30,
            capture_output=True,
        )
    finally:
        tmp.unlink(missing_ok=True)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError("не удалось обработать картинку")


STORE_TTL = 15 * 60  # столько живут найденные версии и скачанные файлы
MAX_PARALLEL = 3
versions: dict[str, tuple[int, float, SearchResult]] = {}  # vid -> (user_id, время, результат поиска)
files: dict[str, tuple[int, float, str, Downloaded]] = {}  # токен -> (user_id, время, vid, файл)
streams: dict[str, tuple[int, float, str]] = {}  # токен потока -> (user_id, время, vid)
played: set[str] = set()  # токены, по которым прослушивание уже записано
proxied: set[str] = set()  # потоки, начатые через прокси YouTube
inflight: dict[tuple[int, str], asyncio.Task] = {}
_sem: asyncio.Semaphore | None = None


def _norm_full(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def _purge() -> None:
    now = time.time()
    for k in [k for k, (_, ts, _) in versions.items() if now - ts > STORE_TTL]:
        versions.pop(k, None)
    for k in [k for k, (_, ts, _, _) in files.items() if now - ts > STORE_TTL]:
        files.pop(k)[3].cleanup()
        played.discard(k)
    for k in [k for k, (_, ts, _) in streams.items() if now - ts > STORE_TTL]:
        streams.pop(k, None)
        played.discard(k)
        proxied.discard(k)


def _ready_file(uid: int, vid: str) -> tuple[str, Downloaded] | None:
    for tok, (u, _, v, d) in files.items():
        if u == uid and v == vid and d.path.exists():
            return tok, d
    return None


async def _get_file(uid: int, vid: str, r: SearchResult) -> tuple[str, Downloaded]:
    """Скачивает версию (один раз на пользователя и версию), возвращает токен файла.
    Одновременные запросы (предзагрузка, «Play», «Save file») ждут одну и ту же загрузку."""
    ready = _ready_file(uid, vid)
    if ready:
        return ready

    async def job() -> tuple[str, Downloaded]:
        global _sem
        _sem = _sem or asyncio.Semaphore(MAX_PARALLEL)
        async with _sem:
            d = await search.fetch_audio(r.url, r)
        tok = secrets.token_urlsafe(18)
        files[tok] = (uid, time.time(), vid, d)
        return tok, d

    key = (uid, vid)
    task = inflight.get(key)
    if task is None:
        task = asyncio.create_task(job())
        inflight[key] = task
        task.add_done_callback(lambda _t: inflight.pop(key, None))
    return await asyncio.shield(task)  # отмена одного запроса не обрывает загрузку для остальных


async def _prefetch_version(uid: int, vid: str, r: SearchResult) -> None:
    """Шторка открыта: первую версию начинаем качать сразу, к нажатию «Play» она уже на диске или в пути."""
    try:
        await _get_file(uid, vid, r)
    except Exception as e:
        logging.info("webapp: предзагрузка версии не удалась %s: %s", r.url, str(e)[:120])


_client: "ClientSession | None" = None


def _http() -> "ClientSession":
    global _client
    if _client is None or _client.closed:
        _client = ClientSession(timeout=ClientTimeout(total=None, sock_connect=10, sock_read=30))
    return _client


def make_app(bot: Bot | None, bot_token: str, bot_username: str, allowed: set[int]) -> web.Application:
    async def authed_user(request: web.Request) -> dict:
        try:
            user = validate_init_data(request.headers.get("X-Init-Data", ""), bot_token)
        except Exception as e:
            logging.info("webapp: отказ в доступе (%s)", e)
            raise web.HTTPUnauthorized(text="unauthorized")
        if allowed and user["id"] not in allowed:
            raise web.HTTPForbidden(text="forbidden")
        return user

    async def index(_: web.Request):
        return web.FileResponse(INDEX, headers={"Cache-Control": "no-store"})

    async def admin_page(_: web.Request):
        return web.FileResponse(INDEX.with_name("admin.html"), headers={"Cache-Control": "no-store"})

    async def admin_only(request: web.Request) -> dict:
        """Админка: только Telegram ID из ADMIN_USERS; остальным 404, чтобы не было понятно, что она есть."""
        try:
            user = validate_init_data(request.headers.get("X-Init-Data", ""), bot_token)
        except Exception:
            raise web.HTTPNotFound()
        if user["id"] not in ADMINS:
            raise web.HTTPNotFound()
        return user

    async def admin_overview(request: web.Request):
        await admin_only(request)
        return web.json_response(await db.admin_overview())

    async def admin_user(request: web.Request):
        await admin_only(request)
        try:
            uid = int(request.query.get("id", ""))
        except ValueError:
            raise web.HTTPBadRequest(text="bad id")
        return web.json_response(await db.admin_user(uid))

    async def dashboard(request: web.Request):
        user = await authed_user(request)
        data = await db.dashboard(user["id"])
        data["user"] = {"id": user["id"], "name": user.get("first_name", "")}
        profile = await db.get_profile(user["id"])
        data["profile"] = {
            "name": profile["name"],
            "tg_name": user.get("first_name", ""),
            "avatar": f"/avatar/{profile['avatar_token']}" if profile["avatar_token"] else None,
            "bg": f"/avatar/{profile['bg_token']}" if profile["bg_token"] else None,
        }
        data["favorites"] = await db.fav_list(user["id"])
        favcache.spawn(favcache.backfill(user["id"], data["favorites"]))  # то, чего нет на диске, догружается в фоне
        data["bot"] = bot_username
        return web.json_response(data)

    async def set_profile(request: web.Request):
        """Имя, под которым дашборд встречает пользователя."""
        user = await authed_user(request)
        name = _clean_name((await request.json()).get("name"))
        if not name:
            raise web.HTTPBadRequest(text="empty name")
        await db.set_display_name(user["id"], name)
        return web.json_response({"name": name})

    async def upload_avatar(request: web.Request):
        """Фото профиля: принимаем картинку, приводим к квадрату 256x256 JPEG."""
        user = await authed_user(request)
        data = await request.read()
        if not data or len(data) > AVATAR_MAX:
            raise web.HTTPBadRequest(text="bad size")
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(16)  # адрес картинки неугадываемый: <img> не умеет слать заголовки авторизации
        dest = AVATAR_DIR / f"{token}.jpg"
        try:
            await asyncio.to_thread(_make_avatar, data, dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise web.HTTPBadRequest(text="bad image")
        old = (await db.get_profile(user["id"]))["avatar_token"]
        await db.set_avatar_token(user["id"], token)
        if old and TOKEN_RE.match(old):
            (AVATAR_DIR / f"{old}.jpg").unlink(missing_ok=True)
        return web.json_response({"avatar": f"/avatar/{token}"})

    async def upload_background(request: web.Request):
        """Большая картинка за страницей: обрезается по центру до 900x1600. Пустое тело = вернуть стандартную."""
        user = await authed_user(request)
        data = await request.read()
        if len(data) > AVATAR_MAX:
            raise web.HTTPBadRequest(text="bad size")
        old = (await db.get_profile(user["id"]))["bg_token"]
        token = ""
        if data:
            AVATAR_DIR.mkdir(parents=True, exist_ok=True)
            token = secrets.token_urlsafe(16)
            dest = AVATAR_DIR / f"{token}.jpg"
            try:
                await asyncio.to_thread(_make_avatar, data, dest, 900, 1600, 5)
            except Exception:
                dest.unlink(missing_ok=True)
                raise web.HTTPBadRequest(text="bad image")
        await db.set_bg_token(user["id"], token)
        if old and TOKEN_RE.match(old):
            (AVATAR_DIR / f"{old}.jpg").unlink(missing_ok=True)
        return web.json_response({"bg": f"/avatar/{token}" if token else None})

    async def fav_play(request: web.Request):
        """Адрес звука песни из избранного. Первый раз песня скачивается и остаётся на диске, дальше отдаётся сразу."""
        user = await authed_user(request)
        body = await request.json()
        artist, title = str(body.get("artist", ""))[:100], str(body.get("title", ""))[:150]
        if not title or not await db.fav_has(user["id"], artist, title):
            raise web.HTTPForbidden(text="not a favorite")  # по этому адресу качаем только то, что в избранном
        try:
            token, path = await favcache.ensure(
                user["id"], artist, title, int(body.get("duration") or 0), str(body.get("cover") or "")
            )
        except Exception as e:
            logging.warning("webapp: избранное не скачалось %s - %s: %s", artist, title, str(e)[:150])
            raise web.HTTPBadGateway(text="download failed")
        return web.json_response({"url": f"/fav/{token}", "ext": path.suffix.lstrip(".")})

    async def serve_fav(request: web.Request):
        path = favcache.find(request.match_info["tok"])  # токен привязан к пользователю и треку, угадать его нельзя
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(
            path,
            headers={
                "Content-Type": favcache.media_type(path),
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )

    async def handoff(request: web.Request):
        """Передаёт очередь плеера в чат с ботом. Закрытая страница звук не играет, а плеер Telegram продолжает."""
        user = await authed_user(request)
        if bot is None:
            raise web.HTTPServiceUnavailable(text="no bot")
        mine = {f["key"]: f for f in await db.fav_list(user["id"])}
        batch = []
        for it in (await request.json()).get("items", [])[:10]:
            fav = mine.get(db.fav_key(str(it.get("artist", "")), str(it.get("title", ""))))
            if fav:
                batch.append(fav)
        if not batch:
            raise web.HTTPBadRequest(text="empty")
        failed = await favlib.play_favorites(bot, user["id"], user["id"], batch, progress=False)
        return web.json_response({"sent": len(batch) - failed, "failed": failed})

    async def serve_avatar(request: web.Request):
        tok = request.match_info["tok"]
        path = AVATAR_DIR / f"{tok}.jpg"
        if not TOKEN_RE.match(tok) or not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    async def favorites(request: web.Request):
        """Добавить трек в «My Favorites» или убрать оттуда. Возвращает весь список."""
        user = await authed_user(request)
        body = await request.json()
        artist, title = str(body.get("artist", ""))[:100], str(body.get("title", ""))[:150]
        if not title:
            raise web.HTTPBadRequest(text="no title")
        op = body.get("op")
        if op == "add":
            cover = str(body.get("cover") or "")
            cover = cover if cover.startswith("https://") and len(cover) < 400 else ""
            duration = int(body.get("duration") or 0)
            await db.fav_add(user["id"], artist, title, duration, cover)
            # звук сохраняем на диск заранее: песня будет играть мгновенно и больше не скачивается заново
            ready = next(
                (
                    d
                    for u, _, v, d in files.values()
                    if u == user["id"] and v == str(body.get("vid", "")) and d.path.exists()
                ),
                None,
            )  # песню уже скачали в шторке: берём этот файл, а не качаем второй раз
            if ready is not None and _norm_full(ready.title) == _norm_full(title):
                try:
                    await favcache.adopt(user["id"], artist, title, ready.path)
                except Exception:
                    logging.exception("webapp: файл не перенесён в избранное")
            if not favcache.has(user["id"], artist, title):
                favcache.spawn(favcache.prefetch(user["id"], artist, title, duration, cover))
        elif op == "remove":
            await db.fav_remove(user["id"], artist, title)
        else:
            raise web.HTTPBadRequest(text="bad op")
        return web.json_response({"favorites": await db.fav_list(user["id"])})

    async def recs(request: web.Request):
        user = await authed_user(request)
        return web.json_response({"items": await _recs(user["id"])})

    async def health(_: web.Request):
        return web.json_response({"ok": True})

    async def resolve(request: web.Request):
        """Версии трека: точное совпадение по названию и длительности — первым."""
        user = await authed_user(request)
        _purge()
        body = await request.json()
        artist, title = str(body.get("artist", ""))[:100], str(body.get("title", ""))[:150]
        want_dur = int(body.get("duration") or 0)
        if not title:
            raise web.HTTPBadRequest(text="no title")
        # fast_only: в шторке нужны скачиваемые версии этой песни, медленные источники (yt-dlp, SoundCloud) не ждём
        out = await search.search(f"{artist} {title}".strip(), 8, fast_only=True)
        want = _norm_full(title)

        def score(r: SearchResult) -> int:
            exact = _norm_full(r.title) == want and (not artist or _norm_full(artist) in _norm_full(r.artist))
            dur_ok = not want_dur or not r.duration or abs(r.duration - want_dur) <= 4
            return 0 if exact and dur_ok else 1 if exact else 2

        items = []
        for r in sorted(out.results, key=score)[:5]:
            vid = secrets.token_urlsafe(8)
            versions[vid] = (user["id"], time.time(), r)
            stok = secrets.token_urlsafe(18)  # <audio> не умеет слать заголовки, поэтому поток — по секретной ссылке
            streams[stok] = (user["id"], time.time(), vid)
            items.append(
                {
                    "vid": vid,
                    "stream": f"/api/stream/{stok}",
                    "title": r.title,
                    "artist": r.artist,
                    "duration": r.duration,
                    "cover": r.thumb_url,
                    "icon": search.ICONS.get(r.source, "🎵"),
                }
            )
        if items:
            top = versions[items[0]["vid"]][2]
            favcache.spawn(_prefetch_version(user["id"], items[0]["vid"], top))
        for it in items[1:3]:  # остальным версиям заранее достаём ссылку на звук: переключение тоже будет быстрым
            favcache.spawn(download.warm(versions[it["vid"]][2].url))
        return web.json_response({"items": items})

    async def stream(request: web.Request):
        """Звук для плеера в шторке, сразу, не дожидаясь полной загрузки. Файл уже на диске — отдаём его;
        иначе YouTube проксируется через сервер (прямая ссылка привязана к IP сервера и у телефона не откроется)."""
        item = streams.get(request.match_info["tok"])
        if not item or time.time() - item[1] > STORE_TTL:
            raise web.HTTPNotFound()
        uid, _, vid = item
        entry = versions.get(vid)
        if not entry:
            raise web.HTTPNotFound()
        r = entry[2]
        if request.match_info["tok"] not in played:
            played.add(request.match_info["tok"])
            await db.log_play(uid, r.title, r.artist, r.duration, r.source, r.url, "audio", r.thumb_url)
        tok = request.match_info["tok"]
        # файл на диске перепакован и по байтам не совпадает с потоком YouTube: начатый поток продолжаем
        # тем же источником, иначе перемотка (запрос с Range) получит куски другого файла
        if tok not in proxied:
            ready = _ready_file(uid, vid)
            path = ready[1].path if ready else audiocache.path_for(r.url)
            if path is not None:
                return web.FileResponse(
                    path, headers={"Content-Type": favcache.media_type(path), "Cache-Control": "no-store"}
                )
        if "youtube.com" in r.url or "youtu.be" in r.url:
            try:
                proxied.add(tok)
                return await _proxy_youtube(request, r.url)
            except web.HTTPException:
                raise
            except Exception as e:
                logging.info("webapp: поток не удался %s: %s", r.url, str(e)[:120])
        # потоком нельзя (другой источник или сбой) — ждём файл целиком
        try:
            _, d = await _get_file(uid, vid, r)
        except Exception:
            raise web.HTTPBadGateway(text="download failed")
        return web.FileResponse(
            d.path, headers={"Content-Type": favcache.media_type(d.path), "Cache-Control": "no-store"}
        )

    async def _proxy_youtube(request: web.Request, url: str) -> web.StreamResponse:
        for attempt in range(2):
            src = await asyncio.to_thread(download.stream_source, url)
            if src is None:
                raise RuntimeError("нет прямой ссылки")
            direct, headers, ctype = src
            asked = request.headers.get("Range")
            # без Range YouTube режет скорость; просим с начала, а клиенту отвечаем обычным 200
            headers = {**headers, "Range": asked or "bytes=0-"}
            up = await _http().get(direct, headers=headers)
            if up.status in (403, 410) and attempt == 0:
                up.release()
                download.forget(url)  # ссылка протухла — извлекаем заново
                continue
            if up.status not in (200, 206):
                up.release()
                raise RuntimeError(f"YouTube ответил {up.status}")
            try:
                resp = web.StreamResponse(status=206 if asked and up.status == 206 else 200)
                resp.headers["Content-Type"] = ctype
                resp.headers["Accept-Ranges"] = "bytes"
                resp.headers["Cache-Control"] = "no-store"
                if "Content-Length" in up.headers:
                    resp.headers["Content-Length"] = up.headers["Content-Length"]
                if asked and "Content-Range" in up.headers:
                    resp.headers["Content-Range"] = up.headers["Content-Range"]
                await resp.prepare(request)
                try:
                    async for chunk in up.content.iter_chunked(64 * 1024):
                        await resp.write(chunk)
                except (ConnectionResetError, asyncio.CancelledError):
                    pass  # плеер сам закрыл соединение (перемотка, пауза, закрытая шторка)
                return resp
            finally:
                up.release()
        raise RuntimeError("ссылка на звук не открылась")

    async def fetch(request: web.Request):
        """mode=file — скачать на сервер и отдать ссылку для плеера; mode=chat — прислать в чат с ботом."""
        user = await authed_user(request)
        body = await request.json()
        entry = versions.get(str(body.get("vid", "")))
        if not entry or entry[0] != user["id"]:
            raise web.HTTPNotFound(text="version expired")
        r, mode = entry[2], body.get("mode")
        try:
            tok, d = await _get_file(user["id"], str(body["vid"]), r)
        except Exception as e:
            logging.warning("webapp: не скачалось %s: %s", r.url, str(e)[:150])
            raise web.HTTPBadGateway(text="download failed")
        if tok not in played:  # одно прослушивание на файл, сколько бы раз его ни запросили
            played.add(tok)
            await db.log_play(user["id"], d.title, d.artist, d.duration, d.source, r.url, "audio")
        if mode == "chat":
            if bot is None:
                raise web.HTTPServiceUnavailable(text="no bot")
            thumb = FSInputFile(d.thumb) if d.thumb else None
            sent = await bot.send_audio(
                user["id"], FSInputFile(d.path), title=d.title, performer=d.artist, duration=d.duration, thumbnail=thumb
            )
            await db.set_cached(f"audio:{r.url}", sent.audio.file_id, d.title, d.artist, d.duration)
            return web.json_response({"ok": True})
        return web.json_response(
            {
                "url": f"/api/file/{tok}",
                "title": d.title,
                "artist": d.artist,
                "duration": d.duration,
                "ext": d.path.suffix.lstrip("."),
                "size": d.path.stat().st_size,
            }
        )

    async def serve_file(request: web.Request):
        # ссылка с секретным токеном: <audio> и загрузчик Telegram не умеют слать заголовки
        item = files.get(request.match_info["tok"])
        if not item or time.time() - item[1] > STORE_TTL or not item[3].path.exists():
            raise web.HTTPNotFound()
        d = item[3]
        name = urllib.parse.quote(f"{d.artist} - {d.title}{d.path.suffix}".strip(" -"))
        ctype = "audio/mpeg" if d.path.suffix == ".mp3" else "audio/mp4"
        return web.FileResponse(
            d.path,
            headers={
                "Content-Type": ctype,
                "Cache-Control": "no-store",
                "Content-Disposition": f"inline; filename*=UTF-8''{name}",
            },
        )

    app = web.Application(client_max_size=AVATAR_MAX + 1024)
    app.router.add_static("/static/", INDEX.parent, show_index=False)  # картинки для страницы
    app.add_routes(
        [
            web.get("/", index),
            web.get("/admin", admin_page),
            web.get("/api/admin", admin_overview),
            web.get("/api/admin/user", admin_user),
            web.get("/healthz", health),
            web.get("/api/dashboard", dashboard),
            web.post("/api/profile", set_profile),
            web.post("/api/avatar", upload_avatar),
            web.post("/api/background", upload_background),
            web.get("/avatar/{tok}", serve_avatar),
            web.post("/api/favorites", favorites),
            web.post("/api/favplay", fav_play),
            web.get("/fav/{tok}", serve_fav),
            web.post("/api/handoff", handoff),
            web.get("/api/recs", recs),
            web.post("/api/resolve", resolve),
            web.post("/api/fetch", fetch),
            web.get("/api/file/{tok}", serve_file),
            web.get("/api/stream/{tok}", stream),
        ]
    )

    async def close_client(_app: web.Application) -> None:
        if _client is not None and not _client.closed:
            await _client.close()

    app.on_cleanup.append(close_client)
    return app


async def start(bot: Bot | None, bot_token: str, bot_username: str, allowed: set[int], port: int) -> web.AppRunner:
    runner = web.AppRunner(make_app(bot, bot_token, bot_username, allowed))
    await runner.setup()
    host = os.getenv("WEBAPP_HOST", "127.0.0.1")  # дома наружу только через туннель; на хостинге 0.0.0.0
    await web.TCPSite(runner, host, port).start()
    logging.info("Мини-апп слушает http://%s:%s", host, port)
    return runner
