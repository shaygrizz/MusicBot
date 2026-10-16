"""Цепочка способов скачивания и проверка результата."""

import asyncio
import logging
import shutil
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .. import ffmpeg
from ..paths import DOWNLOAD_DIR
from .fallbacks import audius_download, hitmo_download, tikwm, title_fallbacks
from .media import MAX_LEADING_SILENCE, fetch_thumb, fetch_thumb_bytes, leading_silence, to_mp3
from .models import MAX_SIZE, Downloaded, SearchResult
from .urls import resolve_short
from .youtube import ALT_YT_CLIENTS, BOT_BLOCK_MARKERS, TOO_BIG, UNAVAILABLE_MARKERS, fast_audio, ytdlp_run


def _clean_artist(info: dict) -> str:
    artist = info.get("artist") or info.get("creator") or ""
    if not artist:
        artist = (info.get("uploader") or info.get("channel") or "").removesuffix(" - Topic")
    return artist


YT_BLOCK_TTL = 120  # секунд помним, что YouTube нас режет, и не тратим время на него


_yt_blocked_until = 0.0


def youtube_block_left() -> float:
    """Сколько секунд ещё пропускаем YouTube после блокировки IP («not a bot»); 0 — не пропускаем."""
    return max(0.0, _yt_blocked_until - time.time())


def _strategies(url: str, kind: str, meta: SearchResult | None, fallbacks: bool = True):
    """Способы скачивания по порядку: (имя, функция, ходит_ли_в_YouTube).
    fallbacks=False — без запасных по названию (их запускают отдельно, один раз: url="title://")."""
    if url == "title://":
        yield from title_fallbacks(url, kind, meta)
        return
    if url.startswith("audius://"):
        yield "audius", (lambda wd: audius_download(url, kind, wd)), False
        return
    if url.startswith("hitmo://"):
        yield "hitmo", (lambda wd: hitmo_download(url, kind, wd)), False
        return
    is_yt = "youtube.com" in url or "youtu.be" in url
    if is_yt and time.time() >= _yt_blocked_until:
        if kind == "audio":
            yield "parallel", (lambda wd: fast_audio(url, wd)), True
        yield "yt-dlp", (lambda wd: ytdlp_run(url, kind, wd, meta)), True
        for client in ALT_YT_CLIENTS:
            extra = {"extractor_args": {"youtube": {"player_client": [client]}}}
            if kind == "audio":
                extra["format"] = "bestaudio/best"
            yield f"yt-dlp:{client}", (lambda wd, extra=extra: ytdlp_run(url, kind, wd, meta, extra)), True
    elif not is_yt:
        yield "yt-dlp", (lambda wd: ytdlp_run(url, kind, wd, meta)), False
        if "tiktok.com" in url:
            yield "tikwm", (lambda wd: tikwm(url, kind, wd)), False
    if fallbacks and is_yt:
        yield from title_fallbacks(url, kind, meta)


_thumb_pool = ThreadPoolExecutor(4, thread_name_prefix="thumb")


def _download_sync(url: str, kind: str, meta: SearchResult | None = None, fallbacks: bool = True) -> Downloaded:
    global _yt_blocked_until
    url = resolve_short(url)
    errors: list[str] = []
    skip_youtube = False
    # обложка качается одновременно со звуком, а не после него (экономит до секунды)
    thumb_bytes = _thumb_pool.submit(fetch_thumb_bytes, meta.thumb_url) if meta and meta.thumb_url else None
    for name, run, uses_yt in _strategies(url, kind, meta, fallbacks):
        if uses_yt and skip_youtube:
            continue
        workdir = DOWNLOAD_DIR / uuid.uuid4().hex[:10]
        workdir.mkdir(parents=True)
        try:
            path, info = run(workdir)
            result = _finish(path, info, kind, meta, workdir, thumb_bytes)
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


def _finish(
    path: Path, info: dict, kind: str, meta: SearchResult | None, workdir: Path, thumb_bytes: Future | None = None
) -> Downloaded:
    if kind == "audio":
        gap = leading_silence(path)
        if gap > MAX_LEADING_SILENCE:  # пустой или «немой» файл — пробуем другой источник
            raise RuntimeError(f"в файле {gap:.0f} с тишины в начале")
    if kind == "audio" and path.suffix.lower() not in {".m4a", ".mp3"}:
        path = to_mp3(path)  # запасной путь: Telegram принимает только m4a/mp3
    if path.stat().st_size > MAX_SIZE:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError("File exceeds 50 MB: Telegram does not let bots send it")

    thumb = None
    if thumb_bytes is not None:
        try:
            data = thumb_bytes.result(timeout=8)
        except Exception:
            data = None
        if data:
            thumb = workdir / "cover.jpg"
            thumb.write_bytes(data)
    elif meta and meta.thumb_url:
        thumb = fetch_thumb(meta.thumb_url, workdir / "cover.jpg")
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


async def download(url: str, kind: str, meta: SearchResult | None = None, fallbacks: bool = True) -> Downloaded:
    return await asyncio.to_thread(_download_sync, url, kind, meta, fallbacks)


def init() -> None:
    """Подготовка при запуске бота: ffmpeg в PATH и чистая папка загрузок (без хвостов прошлого запуска)."""
    ffmpeg.ensure()
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    DOWNLOAD_DIR.mkdir(exist_ok=True)
