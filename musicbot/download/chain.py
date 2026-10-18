"""Цепочка способов скачивания и проверка результата."""

import asyncio
import logging
import shutil
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import yt_dlp

from .. import ffmpeg
from ..paths import DOWNLOAD_DIR
from .fallbacks import audius_download, hitmo_download, tikwm, title_fallbacks
from .media import MAX_LEADING_SILENCE, fetch_thumb, fetch_thumb_bytes, leading_silence, probe_duration, to_mp3
from .models import MAX_SIZE, Downloaded, SearchResult
from .urls import resolve_short
from .youtube import (
    ALT_YT_CLIENTS,
    BLOCKED_MESSAGE,
    TOO_BIG,
    YT_PROXY,
    block_youtube,
    cookies_usable,
    fast_audio,
    is_bot_block,
    is_unavailable,
    youtube_block_left,
    ytdlp_run,
)


def _clean_artist(info: dict) -> str:
    artist = info.get("artist") or info.get("creator") or ""
    if not artist:
        artist = (info.get("uploader") or info.get("channel") or "").removesuffix(" - Topic")
    return artist


TITLE_DURATION_GUARD = 20  # секунд: запасной источник ищет по названию и мог отдать другую запись или отрывок


def _strategies(url: str, kind: str, meta: SearchResult | None, fallbacks: bool = True, ignore_block: bool = False):
    """Способы скачивания по порядку: (имя, функция, ходит_ли_в_YouTube).
    fallbacks=False — без запасных по названию (их запускают отдельно, один раз: url="title://").
    ignore_block=True — пробуем YouTube, даже если недавно он нас блокировал (последний шанс)."""
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
    if is_yt and (ignore_block or not youtube_block_left()):
        if kind == "audio":
            yield "parallel", (lambda wd: fast_audio(url, wd)), True
        yield "yt-dlp", (lambda wd: ytdlp_run(url, kind, wd, meta)), True
        if cookies_usable():
            # устаревшие куки дают «The page needs to be reloaded» и «Requested format is not available» у всех клиентов:
            # раньше других способов пробуем то же самое без куки, чтобы плохой cookies.txt не ломал скачивание
            yield "yt-dlp:nocookies", (lambda wd: ytdlp_run(url, kind, wd, meta, {"cookiefile": None})), True
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


def _download_sync(
    url: str, kind: str, meta: SearchResult | None = None, fallbacks: bool = True, ignore_block: bool = False
) -> Downloaded:
    url = resolve_short(url)
    errors: list[str] = []
    skip_youtube = False
    blocked_before = not ignore_block and youtube_block_left() > 0  # блокировка уже была до этой загрузки
    # обложка качается одновременно со звуком, а не после него (экономит до секунды)
    thumb_bytes = _thumb_pool.submit(fetch_thumb_bytes, meta.thumb_url) if meta and meta.thumb_url else None
    for name, run, uses_yt in _strategies(url, kind, meta, fallbacks, ignore_block):
        if uses_yt and skip_youtube:
            continue
        workdir = DOWNLOAD_DIR / uuid.uuid4().hex[:10]
        workdir.mkdir(parents=True)
        try:
            path, info = run(workdir)
            if url == "title://" and meta and meta.duration:
                # запасные источники ищут по названию: отрывок или другая запись — это «не получилось», пробуем следующий
                real = probe_duration(path)
                if real and abs(real - meta.duration) > TITLE_DURATION_GUARD:
                    raise RuntimeError(f"{name}: длина {real:.0f} с вместо {meta.duration} с")
            result = _finish(path, info, kind, meta, workdir, thumb_bytes)
            logging.info("скачано способом %s: %s", name, url)
            return result
        except Exception as e:
            shutil.rmtree(workdir, ignore_errors=True)
            if TOO_BIG in str(e):
                raise  # другим способом файл меньше не станет
            logging.warning("способ %s не сработал: %s", name, str(e)[:200])
            errors.append(f"{name}: {str(e)[:120]}")
            if uses_yt and is_bot_block(str(e)):
                # остальные клиенты YouTube получат ту же блокировку — идём сразу дальше
                skip_youtube = True
                block_youtube()
            elif uses_yt and is_unavailable(str(e)):
                skip_youtube = True  # именно этот ролик закрыт; YouTube в целом нас не блокирует
    if blocked_before and ("youtube.com" in url or "youtu.be" in url):
        # из-за блокировки YouTube не пробовался вообще (видео, ссылка без названия): блокировка могла уже пройти,
        # поэтому один последний раз идём в него в обход флага
        errors.append(BLOCKED_MESSAGE)
        try:
            return _download_sync(url, kind, meta, False, True)
        except Exception as e:
            errors.append(f"последняя попытка: {str(e)[:160]}")
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


async def download(
    url: str, kind: str, meta: SearchResult | None = None, fallbacks: bool = True, ignore_block: bool = False
) -> Downloaded:
    return await asyncio.to_thread(_download_sync, url, kind, meta, fallbacks, ignore_block)


def init() -> None:
    """Подготовка при запуске бота: ffmpeg в PATH и чистая папка загрузок (без хвостов прошлого запуска)."""
    ffmpeg.ensure()
    logging.info(
        "yt-dlp %s, куки YouTube: %s, прокси: %s",
        yt_dlp.version.__version__,
        "есть" if cookies_usable() else "нет",
        "да" if YT_PROXY else "нет",
    )
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    DOWNLOAD_DIR.mkdir(exist_ok=True)
