"""Постоянный кэш звука для избранного: песня скачивается один раз, дальше играет с диска."""
import asyncio
import hashlib
import hmac
import os
import re
import shutil
from pathlib import Path

import db
import downloader
import paths
import search

CACHE_DIR = paths.FAVORITES
MAX_FILES = 400  # старые файлы удаляются, чтобы папка не росла бесконечно
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_inflight: dict[str, asyncio.Task] = {}


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
    files = sorted((p for p in CACHE_DIR.glob("*.*") if p.suffix in (".m4a", ".mp3")),
                   key=lambda p: p.stat().st_mtime)
    for p in files[:max(0, len(files) - MAX_FILES)]:
        p.unlink(missing_ok=True)


async def _download(token: str, artist: str, title: str, duration: int, cover: str) -> Path:
    lazy = search.lazy_for(artist, title, duration)
    meta = downloader.SearchResult(id="", title=title, artist=artist, duration=duration, url=lazy, thumb_url=cover)
    d = await search.fetch_audio(lazy, meta)  # источник подбирается сам, при сбое пробуются другие версии
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / f"{token}{d.path.suffix.lower()}"
        await asyncio.to_thread(shutil.copyfile, d.path, dest)
    finally:
        d.cleanup()
    _prune()
    return dest


async def ensure(user_id: int, artist: str, title: str, duration: int = 0, cover: str = "") -> tuple[str, Path]:
    """Возвращает (токен, файл). Если песни ещё нет на диске, скачивает её; одновременные запросы ждут один и тот же файл."""
    token = token_for(user_id, artist, title)
    path = find(token)
    if path:
        return token, path
    task = _inflight.get(token)
    if task is None:
        task = asyncio.create_task(_download(token, artist, title, duration, cover))
        _inflight[token] = task
        task.add_done_callback(lambda _t, k=token: _inflight.pop(k, None))
    return token, await asyncio.shield(task)  # отмена запроса не должна обрывать загрузку


async def prefetch(user_id: int, artist: str, title: str, duration: int = 0, cover: str = "") -> None:
    """Фоновая загрузка при добавлении в избранное: к первому прослушиванию файл уже на диске."""
    try:
        await ensure(user_id, artist, title, duration, cover)
    except Exception:
        pass
