"""Скачивание с YouTube: прямая ссылка, параллельная загрузка кусками, запасные клиенты."""

import asyncio
import logging
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

# YouTube закрыл IP сервера («not a bot»): ненадолго отключаем его для всех, чтобы не бить по закрытому адресу
YT_BLOCK_TTL = 120  # секунд
_blocked_until = 0.0

# блокировка всего IP: только этот признак. «Sign in to confirm your age» — возрастное ограничение одного ролика,
# оно IP не закрывает; а «Requested format is not available» лечится другим клиентом YouTube
BOT_BLOCK_MARKERS = ("not a bot",)

# ролик закрыт правообладателем, страной или удалён: другие «клиенты» YouTube не помогут, но сам YouTube не блокирует
UNAVAILABLE_MARKERS = (
    "Video unavailable",
    "Private video",
    "has been removed",
    "no longer available",
    "in your country",
    "copyright",
    "has been terminated",
)

BLOCKED_MESSAGE = "YouTube временно пропущен (недавняя блокировка IP)"

# необязательный прокси для всего трафика YouTube (в том числе скачивания по прямой ссылке: она привязана к IP)
YT_PROXY = os.getenv("YT_PROXY", "").strip()


def block_youtube(seconds: float = YT_BLOCK_TTL) -> None:
    global _blocked_until
    _blocked_until = time.time() + seconds


def youtube_block_left() -> float:
    """Сколько секунд ещё пропускаем YouTube после блокировки IP («not a bot»); 0 — не пропускаем."""
    return max(0.0, _blocked_until - time.time())


def is_bot_block(message: str) -> bool:
    return any(mk in message for mk in BOT_BLOCK_MARKERS)


def is_unavailable(message: str) -> bool:
    return any(mk in message for mk in UNAVAILABLE_MARKERS)


def _fetch_range(url: str, headers: dict, a: int, b: int) -> bytes:
    """Один кусок файла; при сбое сети повторяем с нарастающей паузой (идея из TelegramMusicBot)."""
    last: Exception | None = None
    proxies = {"http": YT_PROXY, "https": YT_PROXY} if YT_PROXY else None
    for attempt in range(3):
        try:
            r = net.session.get(url, headers={**headers, "Range": f"bytes={a}-{b}"}, timeout=30, proxies=proxies)
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


COOKIES_FILE = paths.COOKIES  # необязательно: куки из браузера помогают против блокировок
FORCE_IPV4 = os.getenv("YT_FORCE_IPV4", "1") != "0"


_cookies_checked: tuple[tuple[str, float], bool] | None = None  # ((файл, время изменения), годится ли он)


def cookies_usable() -> bool:
    """cookies.txt есть и он в формате Netscape. Файл в другом формате (например, JSON из расширения браузера)
    yt-dlp не принимает и роняет каждое скачивание; такой файл пропускаем и один раз пишем об этом в лог."""
    global _cookies_checked
    try:
        stamp = (str(COOKIES_FILE), COOKIES_FILE.stat().st_mtime)
    except OSError:
        return False
    if _cookies_checked and _cookies_checked[0] == stamp:
        return _cookies_checked[1]
    try:
        head = COOKIES_FILE.read_text(encoding="utf-8", errors="replace")[:20000].lstrip("\ufeff")
    except OSError:
        head = ""
    lines = [ln for ln in head.splitlines() if ln.strip()]
    ok = bool(lines) and (
        lines[0].startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File"))
        or any(len(ln.split("\t")) == 7 for ln in lines if not ln.startswith("#"))
    )
    if not ok:
        logging.warning(
            "%s не в формате Netscape (нужна первая строка «# Netscape HTTP Cookie File»): работаем без куки",
            COOKIES_FILE,
        )
    _cookies_checked = (stamp, ok)
    return ok


def youtube_opts(opts: dict) -> dict:
    """Общее для всех обращений к YouTube: куки (если положены в DATA_DIR), JS-движок для разбора страницы
    и IPv4. Без этого YouTube чаще отвечает «Sign in to confirm you're not a bot»: подсетям IPv6 дата-центров
    он доверяет меньше, а без JS-движка запросы выглядят не как у браузера."""
    out = dict(opts)
    if cookies_usable():
        out["cookiefile"] = str(COOKIES_FILE)
    if os.getenv("YTDLP_JS_RUNTIME"):  # в образе это Node.js
        out["js_runtimes"] = {os.environ["YTDLP_JS_RUNTIME"]: {}}
    if YT_PROXY:
        out["proxy"] = YT_PROXY
    elif FORCE_IPV4:
        out["source_address"] = "0.0.0.0"
    return out


# Информация о ролике (прямая ссылка на звук) живёт несколько часов — не извлекаем её дважды
_info_cache: dict[str, tuple[float, dict]] = {}


INFO_TTL = 3 * 3600


# прогрев и предзагрузка запрашивают ссылки пачками; без ограничения YouTube быстрее решит, что мы бот, и закроет IP
_extract_slots = threading.BoundedSemaphore(int(os.getenv("YT_EXTRACT_PARALLEL", "3")))


def _yt_info(url: str) -> dict:
    hit = _info_cache.get(url)
    if hit and time.time() - hit[0] < INFO_TTL:
        return hit[1]
    if youtube_block_left():
        raise RuntimeError(BLOCKED_MESSAGE)
    opts = youtube_opts(
        {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 12, "format": YT_AUDIO_FORMAT}
    )
    with _extract_slots:
        hit = _info_cache.get(url)  # пока ждали очереди, ссылку мог достать другой поток
        if hit and time.time() - hit[0] < INFO_TTL:
            return hit[1]
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            if is_bot_block(str(e)):
                block_youtube()  # прогрев и предзагрузка тоже узнают о блокировке и перестают бить по закрытому IP
            raise
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
    if youtube_block_left():
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


# web_embedded добавлен ради возрастных ограничений; без PO-токена все запасные клиенты на обычном ролике отвечают
# «format is not available», поэтому длинный список только удлинял бы путь ошибки
ALT_YT_CLIENTS = ["tv", "web_embedded", "web_safari", "mweb", "ios"]

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
    opts = youtube_opts(opts)
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
