"""Работа с файлами: обложка, перекодирование, тишина и длительность (ffmpeg, ffprobe)."""

import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


def fetch_thumb(url: str, dest: Path) -> Path | None:
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
    """Настоящая длительность файла (ffprobe). 0 — не удалось определить."""
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
