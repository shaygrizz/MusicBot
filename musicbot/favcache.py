"""Постоянный кэш звука для избранного: песня скачивается один раз, дальше играет с диска."""

import asyncio
import hashlib
import hmac
import logging
import os
import re
import shutil
from pathlib import Path

from . import audiocache, db, download, paths, search

CACHE_DIR = paths.FAVORITES
# старые файлы удаляются, чтобы папка не росла бесконечно. Считаем по размеру, а не по числу файлов:
# один добавленный плейлист на сотню песен не должен вытеснять избранное остальных
MAX_BYTES = int(os.getenv("FAV_CACHE_MB", "1500")) * 1024 * 1024
BACKFILL_MAX = 500  # столько песен одного пользователя догружаем за проход
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
PREFETCH_TRIES = 3
_inflight: dict[str, asyncio.Task] = {}
_background: set[asyncio.Task] = set()
_backfilling: set[int] = set()


def token_for(user_id: int, artist: str, title: str) -> str:
    """Адрес файла: подпись от пользователя и трека, поэтому его нельзя угадать и нельзя взять чужой."""
    secret = os.getenv("BOT_TOKEN", "").encode()
    msg = f"{user_id}|{db.fav_key(artist, title)}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def find(token: str) -> Path | None:
    if not TOKEN_RE.match(token):
        return None
    for p in CACHE_DIR.glob(f"{token}.*"):
        if p.suffix in (".m4a", ".mp3") and p.stat().st_size > 0:
            return p
    return None


def media_type(path: Path) -> str:
    return "audio/mpeg" if path.suffix == ".mp3" else "audio/mp4"


def _prune() -> None:
    files = sorted(
        ((p, p.stat()) for p in CACHE_DIR.glob("*.*") if p.suffix in (".m4a", ".mp3")), key=lambda f: f[1].st_mtime
    )
    total = sum(st.st_size for _, st in files)
    for p, st in files:
        if total <= MAX_BYTES:
            break
        p.unlink(missing_ok=True)
        total -= st.st_size


def _place(src: Path, dest: Path) -> None:
    """Жёсткая ссылка на тот же файл (он уже лежит в общем кэше звука на этом же диске), иначе копия."""
    tmp = dest.with_name(dest.name + ".part")
    tmp.unlink(missing_ok=True)
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


async def _download(token: str, artist: str, title: str, duration: int, cover: str, url: str = "") -> Path:
    # ссылка известна (песня из плейлиста YouTube) — качаем её; иначе источник подбирается по названию
    src = url or search.lazy_for(artist, title, duration)
    meta = download.SearchResult(id="", title=title, artist=artist, duration=duration, url=src, thumb_url=cover)
    d = await search.fetch_audio(src, meta)  # при сбое пробуются другие версии
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / f"{token}{d.path.suffix.lower()}"
        cached = audiocache.path_for(src)
        await asyncio.to_thread(_place, cached if cached and cached.suffix == d.path.suffix else d.path, dest)
    finally:
        d.cleanup()
    _prune()
    return dest


async def ensure(
    user_id: int, artist: str, title: str, duration: int = 0, cover: str = "", url: str = ""
) -> tuple[str, Path]:
    """Возвращает (токен, файл). Если песни ещё нет на диске, скачивает её; одновременные запросы ждут один и тот же файл."""
    token = token_for(user_id, artist, title)
    path = find(token)
    if path:
        return token, path
    task = _inflight.get(token)
    if task is None:
        task = asyncio.create_task(_download(token, artist, title, duration, cover, url))
        _inflight[token] = task
        task.add_done_callback(lambda _t, k=token: _inflight.pop(k, None))
    return token, await asyncio.shield(task)  # отмена запроса не должна обрывать загрузку


def has(user_id: int, artist: str, title: str) -> bool:
    return find(token_for(user_id, artist, title)) is not None


def spawn(coro) -> None:
    """Фоновая задача со ссылкой на неё: иначе event loop может собрать её сборщиком мусора на середине."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def adopt(user_id: int, artist: str, title: str, src: Path) -> bool:
    """Кладёт уже скачанный файл в постоянный кэш, чтобы не качать ту же песню второй раз."""
    token = token_for(user_id, artist, title)
    if find(token) or not src.exists() or src.suffix.lower() not in (".m4a", ".mp3"):
        return find(token) is not None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{token}{src.suffix.lower()}"
    await asyncio.to_thread(_place, src, dest)
    _prune()
    return True


async def prefetch(user_id: int, artist: str, title: str, duration: int = 0, cover: str = "", url: str = "") -> None:
    """Фоновая загрузка при добавлении в избранное: к первому прослушиванию файл уже на диске.
    При сбое пробует ещё раз: иначе песня скачивалась бы только при нажатии, и пользователь ждал бы."""
    for attempt in range(PREFETCH_TRIES):
        try:
            await ensure(user_id, artist, title, duration, cover, url)
            return
        except Exception as e:
            logging.warning(
                "избранное не предзагрузилось (%d/%d) %s - %s: %s",
                attempt + 1,
                PREFETCH_TRIES,
                artist,
                title,
                str(e)[:150],
            )
            await asyncio.sleep(5 * (attempt + 1))


async def backfill(user_id: int, favs: list[dict]) -> None:
    """Догружает избранное, которого нет на диске (добавлено раньше, сорвалась загрузка, файл вытеснен).
    Идёт по одной песне, у одного пользователя не больше одного прохода сразу."""
    if user_id in _backfilling:
        return
    _backfilling.add(user_id)
    try:
        for f in favs[:BACKFILL_MAX]:
            if not has(user_id, f["artist"], f["title"]):
                await prefetch(
                    user_id, f["artist"], f["title"], f.get("duration") or 0, f.get("cover") or "", f.get("url") or ""
                )
    finally:
        _backfilling.discard(user_id)
