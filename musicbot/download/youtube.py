"""Скачивание с YouTube: прямая ссылка, параллельная загрузка кусками, запасные клиенты."""

import asyncio
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

from .. import net, paths
from .models import MAX_SIZE, SearchResult

PARALLEL_PARTS = 6


def _fetch_range(url: str, headers: dict, a: int, b: int) -> bytes:
    """Один кусок файла; при сбое сети повторяем с нарастающей паузой (идея из TelegramMusicBot)."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = net.session.get(url, headers={**headers, "Range": f"bytes={a}-{b}"}, timeout=30)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last = e
            time.sleep(0.4 * 2**attempt)
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


# прогрев и предзагрузка запрашивают ссылки пачками; без ограничения YouTube быстрее решит, что мы бот, и закроет IP
_extract_slots = threading.BoundedSemaphore(int(os.getenv("YT_EXTRACT_PARALLEL", "3")))


def _yt_info(url: str) -> dict:
    hit = _info_cache.get(url)
    if hit and time.time() - hit[0] < INFO_TTL:
        return hit[1]
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 12, "format": YT_AUDIO_FORMAT}
    with _extract_slots:
        hit = _info_cache.get(url)  # пока ждали очереди, ссылку мог достать другой поток
        if hit and time.time() - hit[0] < INFO_TTL:
            return hit[1]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    if "entries" in info:
        info = info["entries"][0]
    if len(_info_cache) > 300:
        _info_cache.pop(next(iter(_info_cache)))
    _info_cache[url] = (time.time(), info)
    return info


def stream_source(url: str) -> tuple[str, dict, str] | None:
    """Прямая ссылка на звук для потокового воспроизведения: (ссылка, заголовки, тип). None — потоком нельзя."""
    info = _yt_info(url)
    if not info.get("url") or info.get("protocol") not in ("https", "http"):
        return None
    ctype = "audio/mp4" if info.get("ext") == "m4a" else "audio/webm"
    return info["url"], dict(info.get("http_headers") or {}), ctype


def forget(url: str) -> None:
    """Ссылка протухла (403): в следующий раз извлечём заново."""
    _info_cache.pop(url, None)


async def warm(url: str) -> None:
    """Заранее достаём ссылку на звук: когда пользователь нажмёт на трек, этот шаг (2–3 с) уже сделан."""
    if "youtube.com" not in url and "youtu.be" not in url:
        return
    try:
        await asyncio.to_thread(_yt_info, url)
    except Exception:
        pass


def fast_audio(url: str, workdir: Path) -> tuple[Path, dict]:
    """Достаём прямую ссылку через yt-dlp и качаем несколькими потоками —
    YouTube ограничивает скорость одного соединения."""
    info = _yt_info(url)
    direct = info.get("url")
    size = info.get("filesize") or info.get("filesize_approx")
    if not direct or not size or info.get("protocol") not in ("https", "http"):
        raise RuntimeError("нужна обычная загрузка")
    headers = dict(info.get("http_headers") or {})
    step = size // PARALLEL_PARTS + 1
    ranges = [(i * step, min(size - 1, (i + 1) * step - 1)) for i in range(PARALLEL_PARTS) if i * step < size]
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
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c", "copy", "-movflags", "+faststart", str(out)],
            check=True,
        )
        raw.unlink(missing_ok=True)
        return out, info
    return raw, info


COOKIES_FILE = paths.COOKIES  # необязательно: куки из браузера помогают против блокировок

ALT_YT_CLIENTS = ["tv", "web_safari", "mweb", "ios"]

TOO_BIG = "File exceeds 50 MB"


def ytdlp_run(
    url: str, kind: str, workdir: Path, meta: SearchResult | None, extra: dict | None = None
) -> tuple[Path, dict]:
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
            opts["postprocessors"] = [{"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}]
    opts.update(extra or {})

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]
    media = [p for p in workdir.iterdir() if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}]
    if not media:
        raise RuntimeError("Не удалось получить файл")
    return media[0], info


# блокировка всего IP: только эти признаки; одиночный 403/429 на одном ролике не повод отключать YouTube для всех песен
BOT_BLOCK_MARKERS = ("not a bot", "Sign in to confirm")

# ролик закрыт правообладателем или удалён: другие «клиенты» YouTube не помогут, но сам YouTube не блокирует
UNAVAILABLE_MARKERS = ("Video unavailable", "blocked", "not available", "Private video", "removed", "copyright")
