import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import paths
import static_ffmpeg
import yt_dlp

static_ffmpeg.add_paths()  # скачает ffmpeg при первом запуске и добавит в PATH

DOWNLOAD_DIR = Path("downloads")
shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)  # чистим хвосты прошлого запуска
DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_SIZE = 49 * 1024 * 1024  # лимит Telegram для ботов ~50 МБ

URL_RE = re.compile(r"https?://\S+")

_ytm = None


@dataclass
class SearchResult:
    id: str
    title: str
    artist: str
    duration: int
    url: str
    thumb_url: str = ""
    source: str = ""  # ytm / ytmv / yt / sc — откуда результат
    verified: bool = False  # совпал с записью каталога (Deezer/iTunes)
    label: str = ""  # тип версии: Original, Remix, Live, Slowed…
    exact: bool = False  # точный трек: то же название и исполнитель без пометок, длительность как в каталоге


@dataclass
class Downloaded:
    path: Path
    thumb: Path | None
    title: str
    artist: str
    duration: int
    source: str
    workdir: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def unwrap_url(url: str) -> str:
    """Достаёт настоящую ссылку из обёрток вроде tiktok.com/login?redirect_url=... и чистит трекинг."""
    for _ in range(3):
        p = urllib.parse.urlparse(url)
        host = p.netloc.lower()
        if "tiktok.com" in host and p.path.startswith("/login"):
            target = urllib.parse.parse_qs(p.query).get("redirect_url")
            if not target:
                break
            url = target[0]
            continue
        break
    p = urllib.parse.urlparse(url)
    if "tiktok.com" in p.netloc.lower() and "/video/" in p.path:
        url = urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))  # без _r, _t и т.п.
    return url


def resolve_short(url: str) -> str:
    """Короткие ссылки TikTok (vm./vt./ /t/) разворачиваем в полные."""
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    tiktok_short = host.endswith("tt.site") or (
        "tiktok.com" in host and (host.split(".")[0] in ("vm", "vt") or p.path.startswith("/t/"))
    )
    if tiktok_short:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return unwrap_url(r.geturl())
        except Exception:
            pass
    return url


def extract_url(text: str) -> str | None:
    m = URL_RE.search(text)
    return unwrap_url(m.group(0).rstrip(").,")) if m else None


def _get_ytm():
    global _ytm
    if _ytm is None:
        from ytmusicapi import YTMusic
        _ytm = YTMusic()
    return _ytm


def _small_thumb(url: str) -> str:
    # googleusercontent-обложки умеют отдавать нужный размер
    return re.sub(r"=w\d+-h\d+.*$", "=w320-h320-l90-rj", url) if "=w" in url else url


def _search_ytm_sync(query: str, limit: int) -> list[SearchResult]:
    ytm = _get_ytm()
    items = ytm.search(query, filter="songs", limit=limit)
    if not items:
        items = ytm.search(query, filter="videos", limit=limit)
    results = []
    for e in items[:limit]:
        vid = e.get("videoId")
        if not vid:
            continue
        artists = e.get("artists") or []
        thumbs = e.get("thumbnails") or []
        results.append(
            SearchResult(
                id=vid,
                title=e.get("title") or "Без названия",
                artist=", ".join(a["name"] for a in artists if a.get("name")),
                duration=int(e.get("duration_seconds") or 0),
                url=f"https://www.youtube.com/watch?v={vid}",
                thumb_url=_small_thumb(thumbs[-1]["url"]) if thumbs else "",
            )
        )
    return results


def _search_ytdlp_sync(query: str, limit: int) -> list[SearchResult]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for e in info.get("entries", []) or []:
        if not e or not e.get("id"):
            continue
        results.append(
            SearchResult(
                id=e["id"],
                title=e.get("title") or "Без названия",
                artist=e.get("uploader") or e.get("channel") or "",
                duration=int(e.get("duration") or 0),
                url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            )
        )
    return results


def _search_sync(query: str, limit: int) -> list[SearchResult]:
    try:
        res = _search_ytm_sync(query, limit)
        if res:
            return res
    except Exception:
        pass
    return _search_ytdlp_sync(query, limit)


async def search(query: str, limit: int = 6) -> list[SearchResult]:
    return await asyncio.to_thread(_search_sync, query, limit)


def _clean_artist(info: dict) -> str:
    artist = info.get("artist") or info.get("creator") or ""
    if not artist:
        artist = (info.get("uploader") or info.get("channel") or "").removesuffix(" - Topic")
    return artist


def _fetch_thumb(url: str, dest: Path) -> Path | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
        if len(data) > 190_000:
            return None
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


def _to_mp3(src: Path) -> Path:
    dst = src.with_suffix(".mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-b:a", "192k", str(dst)],
        check=True,
    )
    src.unlink(missing_ok=True)
    return dst


PARALLEL_PARTS = 6


def _fetch_range(url: str, headers: dict, a: int, b: int) -> bytes:
    """Один кусок файла; при сбое сети повторяем с нарастающей паузой (идея из TelegramMusicBot)."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={**headers, "Range": f"bytes={a}-{b}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(0.4 * 2 ** attempt)
    raise last  # type: ignore[misc]


# Качество звука из YouTube: AUDIO_QUALITY=fast в .env даёт файлы примерно в 2-3 раза меньше (AAC около 48 кбит/с),
# а значит и скачивание в 2-3 раза быстрее; по умолчанию стандартное качество (AAC 128 кбит/с)
YT_AUDIO_FORMAT = (
    "bestaudio[ext=m4a][abr<=64]/bestaudio[ext=m4a]/bestaudio"
    if os.getenv("AUDIO_QUALITY", "").strip().lower() == "fast"
    else "bestaudio[ext=m4a]/bestaudio"
)

# Информация о ролике (прямая ссылка на звук) живёт несколько часов — не извлекаем её дважды
_info_cache: dict[str, tuple[float, dict]] = {}
INFO_TTL = 3 * 3600


def _yt_info(url: str) -> dict:
    hit = _info_cache.get(url)
    if hit and time.time() - hit[0] < INFO_TTL:
        return hit[1]
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 12,
            "format": YT_AUDIO_FORMAT}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if "entries" in info:
        info = info["entries"][0]
    if len(_info_cache) > 300:
        _info_cache.pop(next(iter(_info_cache)))
    _info_cache[url] = (time.time(), info)
    return info


async def warm(url: str) -> None:
    """Заранее достаём ссылку на звук: когда пользователь нажмёт на трек, этот шаг (2–3 с) уже сделан."""
    if "youtube.com" not in url and "youtu.be" not in url:
        return
    try:
        await asyncio.to_thread(_yt_info, url)
    except Exception:
        pass


def _fast_audio(url: str, workdir: Path) -> tuple[Path, dict]:
    """Достаём прямую ссылку через yt-dlp и качаем несколькими потоками —
    YouTube ограничивает скорость одного соединения."""
    info = _yt_info(url)
    direct = info.get("url")
    size = info.get("filesize") or info.get("filesize_approx")
    if not direct or not size or info.get("protocol") not in ("https", "http"):
        raise RuntimeError("нужна обычная загрузка")
    headers = dict(info.get("http_headers") or {})
    step = size // PARALLEL_PARTS + 1
    ranges = [(i * step, min(size - 1, (i + 1) * step - 1)) for i in range(PARALLEL_PARTS)
              if i * step < size]
    try:
        with ThreadPoolExecutor(len(ranges)) as ex:
            parts = list(ex.map(lambda r: _fetch_range(direct, headers, *r), ranges))
    except Exception:
        _info_cache.pop(url, None)  # ссылка могла протухнуть — в следующий раз извлечём заново
        raise
    raw = workdir / f"{info['id']}.raw.{info.get('ext', 'm4a')}"
    raw.write_bytes(b"".join(parts))
    if raw.stat().st_size < size * 0.99:
        raise RuntimeError("файл скачался не полностью")
    if info.get("ext") == "m4a":  # DASH-контейнер -> обычный m4a, копирование без перекодирования
        out = workdir / f"{info['id']}.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c", "copy",
             "-movflags", "+faststart", str(out)],
            check=True,
        )
        raw.unlink(missing_ok=True)
        return out, info
    return raw, info


COOKIES_FILE = paths.COOKIES  # необязательно: куки из браузера помогают против блокировок
ALT_YT_CLIENTS = ["tv", "web_safari", "mweb", "ios"]
TOO_BIG = "File exceeds 50 MB"


def _ytdlp_run(url: str, kind: str, workdir: Path, meta: SearchResult | None,
               extra: dict | None = None) -> tuple[Path, dict]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "max_filesize": MAX_SIZE * 4,
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 8,
        "retries": 3,
        "extractor_retries": 2,
        "socket_timeout": 20,
    }
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    if os.getenv("YTDLP_JS_RUNTIME"):  # YouTube просит JS-движок для разбора страницы; в образе это Node.js
        opts["js_runtimes"] = {os.environ["YTDLP_JS_RUNTIME"]: {}}
    if kind == "audio":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        opts["format"] = "best[filesize<48M][ext=mp4]/best[ext=mp4]/bv*[height<=720]+ba/best"
        opts["merge_output_format"] = "mp4"
        if not meta or not meta.thumb_url:
            opts["writethumbnail"] = True
            opts["postprocessors"] = [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}
            ]
    opts.update(extra or {})

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]
    media = [p for p in workdir.iterdir() if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}]
    if not media:
        raise RuntimeError("Не удалось получить файл")
    return media[0], info


MAX_LEADING_SILENCE = 12  # секунд тишины в начале, после которых файл считаем браком
BAD_VERSION_RE = re.compile(
    r"\b(skip\w*|slowed|sped\s*up|speed\s*up|nightcore|reverb|8d|bass\s*boost(?:ed)?|karaoke|"
    r"instrumental|covers?|live|remix|mashup|loop|hours?|preview|snippet|a\s*cappella|acapella|fingerstyle|"
    r"lullaby|arrangement|choreography|tutorial|reaction|parody|piano\s+(?:version|cover|ver\.?)|"
    r"кавер\w*|ремикс\w*|караоке|замедл\w*|ускор\w*)\b",
    re.I,
)


def _leading_silence(path: Path) -> float:
    """Сколько секунд тишины в начале файла (проверяем первые 40 с). 0 — звук есть сразу."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-t", "40", "-i", str(path),
         "-af", "silencedetect=noise=-50dB:d=2", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    start = end = None
    for line in r.stderr.splitlines():
        if "silence_start" in line and start is None:
            start = float(line.rsplit(":", 1)[1])
        elif "silence_end" in line and start is not None and end is None:
            end = float(line.split("silence_end:")[1].split("|")[0])
    if start is None or start > 1:
        return 0.0
    return end if end is not None else 40.0  # тихо все проверенные 40 с


def probe_duration(path: Path) -> float:
    """Настоящая длительность файла (ffprobe). 0 — не удалось определить."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _sc_candidates(query: str, meta: SearchResult | None) -> list[str]:
    """Ссылки на SoundCloud: сначала подходящие по длительности и без «странных» версий."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
            "socket_timeout": 10, "retries": 1, "extractor_retries": 1}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch8:{query}", download=False)
    artist_tokens = [_n(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_n(t)) >= 3] if meta else []
    scored = []
    for e in info.get("entries", []) or []:
        link, title = e.get("url"), e.get("title") or ""
        if not link:
            continue
        if BAD_VERSION_RE.search(title) and not BAD_VERSION_RE.search(query):
            continue
        if artist_tokens and not any(t in _n(f"{e.get('uploader') or ''} {title}") for t in artist_tokens):
            continue  # песня с таким же названием, но другого исполнителя: лучше ничего, чем чужая
        dur = e.get("duration")
        if meta and meta.duration and dur and abs(dur - meta.duration) > 10:
            continue
        diff = abs(dur - meta.duration) if (meta and meta.duration and dur) else 5
        scored.append((diff, link))
    scored.sort(key=lambda x: x[0])
    return [link for _, link in scored[:3]]


def _soundcloud(query: str, kind: str, workdir: Path, meta: SearchResult | None):
    last_err = "на SoundCloud нет подходящей версии"
    extra = {"socket_timeout": 10, "retries": 1, "extractor_retries": 1}
    for link in _sc_candidates(query, meta):
        try:
            path, info = _ytdlp_run(link, kind, workdir, meta, extra)
            gap = _leading_silence(path)
            if gap > MAX_LEADING_SILENCE:
                last_err = f"версия начинается с {gap:.0f} с тишины"
                path.unlink(missing_ok=True)
                continue
            return path, info
        except Exception as e:
            last_err = str(e)[:150]
    raise RuntimeError(last_err)


def _tikwm(url: str, kind: str, workdir: Path):
    """Запасной источник для TikTok, когда сам TikTok просит войти в аккаунт."""
    import json
    api = "https://www.tikwm.com/api/?hd=1&url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.load(r)
    data = resp.get("data") or {}
    if resp.get("code") != 0 or not data:
        raise RuntimeError(f"tikwm: {resp.get('msg') or 'пустой ответ'}")

    music = data.get("music_info") or {}
    if kind == "audio":
        media_url = data.get("music") or music.get("play")
        suffix = ".audio"  # формат неизвестен — _finish прогонит через ffmpeg в mp3
    else:
        media_url = data.get("hdplay") or data.get("play")
        suffix = ".mp4"
    if not media_url:
        raise RuntimeError("tikwm: нет ссылки на файл")

    path = workdir / f"{data.get('id', 'tiktok')}{suffix}"
    req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        path.write_bytes(r.read())
    author = data.get("author") or {}
    info = {
        "id": str(data.get("id", "")),
        "title": (data.get("title") or "TikTok")[:200],
        "track": music.get("title") if kind == "audio" else None,
        "artist": music.get("author") if kind == "audio" else None,
        "uploader": author.get("nickname") or author.get("unique_id") or "",
        "duration": int(music.get("duration") or 0) if kind == "audio" else int(data.get("duration") or 0),
        "extractor_key": "TikTok",
    }
    return path, info


# блокировка всего IP: только эти признаки; одиночный 403/429 на одном ролике не повод отключать YouTube для всех песен
BOT_BLOCK_MARKERS = ("not a bot", "Sign in to confirm")
# ролик закрыт правообладателем или удалён: другие «клиенты» YouTube не помогут, но сам YouTube не блокирует
UNAVAILABLE_MARKERS = ("Video unavailable", "blocked", "not available", "Private video", "removed", "copyright")

DYDKI_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _n(s: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\(.*?\)|\[.*?\]", " ", s.lower()))


def _dydki(meta: SearchResult, workdir: Path):
    """Самый последний запасной источник: сайт-агрегатор готовых mp3 (по мотивам TelegramMusicBot).
    Метаданные там шумные, поэтому берём только строгое совпадение названия и исполнителя."""
    from bs4 import BeautifulSoup
    query = f"{meta.artist} {meta.title}".strip()
    req = urllib.request.Request("https://dydki.net/?mp3=" + urllib.parse.quote(query), headers=DYDKI_UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        soup = BeautifulSoup(r.read().decode("utf-8", "ignore"), "html.parser")
    box = soup.find("div", class_="results")
    if box is None:
        raise RuntimeError("dydki: нет результатов")
    want_title, want_artist = _n(meta.title), [_n(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_n(t)) >= 3]
    asked_bad = bool(BAD_VERSION_RE.search(query))
    best = None
    for el in box.find_all("div", class_="chkd"):
        a, t, mp3 = el.find(class_="track__artist"), el.find(class_="track__title"), el.get("data-mp3")
        if not (a and t and isinstance(mp3, str)):
            continue
        title_text, artist_text = t.text.strip(), a.text.strip()
        if want_title and want_title not in _n(title_text):
            continue
        if want_artist and not any(w in _n(artist_text + title_text) for w in want_artist):
            continue
        if BAD_VERSION_RE.search(title_text) and not asked_bad:
            continue
        best = mp3
        break
    if not best:
        raise RuntimeError("dydki: нет подходящей версии")
    path = workdir / "dydki.mp3"
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(best, headers=DYDKI_UA), timeout=60) as r:
                data = r.read()
            if len(data) < 100_000:
                raise RuntimeError("dydki: файл слишком маленький")
            path.write_bytes(data)
            return path, {"id": "dydki", "extractor_key": "dydki"}
        except Exception as e:
            last = e
            time.sleep(0.5 * 2 ** attempt)
    raise last  # type: ignore[misc]
YT_BLOCK_TTL = 120  # секунд помним, что YouTube нас режет, и не тратим время на него
_yt_blocked_until = 0.0
YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})")


def _sc_query(url: str, meta: SearchResult | None) -> str | None:
    """Строка для поиска в SoundCloud: из метаданных поиска или из YouTube Music по id."""
    if meta and meta.title:
        return f"{meta.artist} {meta.title}".strip()
    m = YT_ID_RE.search(url)
    if m:
        try:
            d = _get_ytm().get_song(m.group(1)).get("videoDetails", {})
            if d.get("title"):
                return f"{d.get('author', '')} {d['title']}".strip()
        except Exception:
            pass
    return None


def _audius_download(url: str, kind: str, workdir: Path):
    import audius
    if kind != "audio":
        raise RuntimeError("Audius — только аудио")
    track_id = url.removeprefix("audius://")
    path = workdir / f"{track_id}.mp3"
    audius.download(track_id, path)
    return path, {"id": track_id, "extractor_key": "Audius"}


def _hitmo_download(url: str, kind: str, workdir: Path):
    import hitmo
    if kind != "audio":
        raise RuntimeError("hitmo: только аудио")
    path = workdir / "hitmo.mp3"
    hitmo.download(urllib.parse.unquote(url.removeprefix("hitmo://")), path)
    return path, {"id": "hitmo", "extractor_key": "hitmo"}


def _hitmo_by_title(meta: SearchResult, workdir: Path):
    """Запасной путь: ищем трек на hitmotop по названию и исполнителю, берём совпадение по названию и длительности."""
    import hitmo
    asked_bad = bool(BAD_VERSION_RE.search(meta.title))
    want = _n(meta.title)
    artist_tokens = [_n(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_n(t)) >= 3]
    for t in hitmo.search(f"{meta.artist} {meta.title}".strip(), 12):
        text = _n(t["artist"] + t["title"])
        if want and want not in _n(t["title"]):
            continue
        if artist_tokens and not any(a in text for a in artist_tokens):
            continue
        if meta.duration and t["duration"] and abs(t["duration"] - meta.duration) > 8:
            continue
        if BAD_VERSION_RE.search(t["title"]) and not asked_bad:
            continue
        return _hitmo_download("hitmo://" + urllib.parse.quote(t["url"], safe=""), "audio", workdir)
    raise RuntimeError("hitmo: нет подходящей версии")


def _title_fallbacks(url: str, kind: str, meta: SearchResult | None):
    """Запасные способы по названию (не по ссылке): SoundCloud, hitmotop, затем dydki."""
    query = _sc_query(url, meta)
    if kind == "audio" and query:
        yield "soundcloud", (lambda wd: _soundcloud(query, kind, wd, meta)), False
    if kind == "audio" and meta and meta.title:
        yield "hitmo", (lambda wd: _hitmo_by_title(meta, wd)), False
    if kind == "audio" and meta and meta.title and meta.artist:
        yield "dydki", (lambda wd: _dydki(meta, wd)), False  # последний шанс


def _strategies(url: str, kind: str, meta: SearchResult | None, fallbacks: bool = True):
    """Способы скачивания по порядку: (имя, функция, ходит_ли_в_YouTube).
    fallbacks=False — без запасных по названию (их запускают отдельно, один раз: url="title://")."""
    if url == "title://":
        yield from _title_fallbacks(url, kind, meta)
        return
    if url.startswith("audius://"):
        yield "audius", (lambda wd: _audius_download(url, kind, wd)), False
        return
    if url.startswith("hitmo://"):
        yield "hitmo", (lambda wd: _hitmo_download(url, kind, wd)), False
        return
    is_yt = "youtube.com" in url or "youtu.be" in url
    if is_yt and time.time() >= _yt_blocked_until:
        if kind == "audio":
            yield "parallel", (lambda wd: _fast_audio(url, wd)), True
        yield "yt-dlp", (lambda wd: _ytdlp_run(url, kind, wd, meta)), True
        for client in ALT_YT_CLIENTS:
            extra = {"extractor_args": {"youtube": {"player_client": [client]}}}
            if kind == "audio":
                extra["format"] = "bestaudio/best"
            yield f"yt-dlp:{client}", (
                lambda wd, extra=extra: _ytdlp_run(url, kind, wd, meta, extra)
            ), True
    elif not is_yt:
        yield "yt-dlp", (lambda wd: _ytdlp_run(url, kind, wd, meta)), False
        if "tiktok.com" in url:
            yield "tikwm", (lambda wd: _tikwm(url, kind, wd)), False
    if fallbacks and is_yt:
        yield from _title_fallbacks(url, kind, meta)


def _download_sync(url: str, kind: str, meta: SearchResult | None = None,
                   fallbacks: bool = True) -> Downloaded:
    global _yt_blocked_until
    url = resolve_short(url)
    errors: list[str] = []
    skip_youtube = False
    for name, run, uses_yt in _strategies(url, kind, meta, fallbacks):
        if uses_yt and skip_youtube:
            continue
        workdir = DOWNLOAD_DIR / uuid.uuid4().hex[:10]
        workdir.mkdir(parents=True)
        try:
            path, info = run(workdir)
            result = _finish(path, info, kind, meta, workdir)
            logging.info("скачано способом %s: %s", name, url)
            return result
        except Exception as e:
            shutil.rmtree(workdir, ignore_errors=True)
            if TOO_BIG in str(e):
                raise  # другим способом файл меньше не станет
            logging.warning("способ %s не сработал: %s", name, str(e)[:200])
            errors.append(f"{name}: {str(e)[:120]}")
            if uses_yt and any(mk in str(e) for mk in BOT_BLOCK_MARKERS):
                # остальные клиенты YouTube получат ту же блокировку — идём сразу дальше
                skip_youtube = True
                _yt_blocked_until = time.time() + YT_BLOCK_TTL
            elif uses_yt and any(mk in str(e) for mk in UNAVAILABLE_MARKERS):
                skip_youtube = True  # именно этот ролик закрыт; YouTube в целом нас не блокирует
    if not errors and "youtu" in url and time.time() < _yt_blocked_until:
        errors.append("YouTube временно пропущен (недавняя блокировка IP)")
    raise RuntimeError("Все способы скачивания не сработали: " + " | ".join(errors))


def _finish(path: Path, info: dict, kind: str, meta: SearchResult | None,
            workdir: Path) -> Downloaded:
    if kind == "audio":
        gap = _leading_silence(path)
        if gap > MAX_LEADING_SILENCE:  # пустой или «немой» файл — пробуем другой источник
            raise RuntimeError(f"в файле {gap:.0f} с тишины в начале")
    if kind == "audio" and path.suffix.lower() not in {".m4a", ".mp3"}:
        path = _to_mp3(path)  # запасной путь: Telegram принимает только m4a/mp3
    if path.stat().st_size > MAX_SIZE:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError("File exceeds 50 MB: Telegram does not let bots send it")

    thumb = None
    if meta and meta.thumb_url:
        thumb = _fetch_thumb(meta.thumb_url, workdir / "cover.jpg")
    if thumb is None:
        jpgs = [p for p in workdir.iterdir() if p.suffix.lower() == ".jpg"]
        thumb = jpgs[0] if jpgs else None

    return Downloaded(
        path=path,
        thumb=thumb,
        title=(meta.title if meta else None) or info.get("track") or info.get("title") or "Без названия",
        artist=(meta.artist if meta else None) or _clean_artist(info),
        duration=int(info.get("duration") or (meta.duration if meta else 0) or 0),
        source=info.get("extractor_key") or "web",
        workdir=workdir,
    )


async def download(url: str, kind: str, meta: SearchResult | None = None,
                   fallbacks: bool = True) -> Downloaded:
    return await asyncio.to_thread(_download_sync, url, kind, meta, fallbacks)
