"""Работа с файлами: обложка, перекодирование, тишина и длительность (ffmpeg, ffprobe)."""

import subprocess
from pathlib import Path

from mutagen import File as MutagenFile

from .. import net


def fetch_thumb_bytes(url: str) -> bytes | None:
    """Обложка для Telegram: не больше ~190 КБ, иначе он её не примет."""
    try:
        data = net.get_bytes(url, timeout=8)
    except Exception:
        return None
    return data if len(data) <= 190_000 else None


def fetch_thumb(url: str, dest: Path) -> Path | None:
    data = fetch_thumb_bytes(url)
    if not data:
        return None
    dest.write_bytes(data)
    return dest


def to_mp3(src: Path) -> Path:
    dst = src.with_suffix(".mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-b:a", "192k", str(dst)],
        check=True,
    )
    src.unlink(missing_ok=True)
    return dst


MAX_LEADING_SILENCE = 12  # секунд тишины в начале, после которых файл считаем браком


def leading_silence(path: Path) -> float:
    """Сколько секунд тишины в начале файла (проверяем первые 40 с). 0 — звук есть сразу."""
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-t",
            "40",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-50dB:d=2",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
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
    """Настоящая длительность файла. mutagen читает заголовок за доли миллисекунды;
    ffprobe (отдельный процесс, ~0,6 с) — только если mutagen не справился. 0 — не удалось определить."""
    try:
        info = MutagenFile(path)
        if info is not None and info.info and info.info.length:
            return float(info.info.length)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0
