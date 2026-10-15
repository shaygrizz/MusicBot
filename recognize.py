"""Распознавание песни по присланному аудио, голосовому, видео или кружку (ShazamIO, неофициальный клиент Shazam)."""
import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import static_ffmpeg

static_ffmpeg.add_paths()

SAMPLE_SECONDS = 15  # Shazam достаточно 5-15 секунд чистого звука


@dataclass
class Recognized:
    title: str
    artist: str
    cover: str = ""
    url: str = ""


def _duration(src: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(src)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _sample(src: Path, dst: Path, start: float) -> None:
    """Короткий моно-отрывок: из любого формата (голосовое, mp3, видео, кружок) в wav."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.1f}", "-i", str(src), "-t", str(SAMPLE_SECONDS),
         "-vn", "-ac", "1", "-ar", "44100", str(dst)],
        check=True, timeout=60,
    )


HERE = Path(__file__).parent
WORKER_PY = Path(os.getenv("SHAZAM_PYTHON") or HERE / ".venv312" / "Scripts" / "python.exe")  # дома у ShazamIO отдельное окружение 3.12 (под 3.14 сборки нет); в образе всё на 3.12
WORKER = HERE / "shazam_worker.py"


async def _ask_shazam(sample: Path) -> Recognized | None:
    if not WORKER_PY.exists():
        raise RuntimeError("нет окружения .venv312 с ShazamIO")
    proc = await asyncio.create_subprocess_exec(
        str(WORKER_PY), str(WORKER), str(sample),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 60)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("Shazam не ответил за 60 с")
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-300:])
    lines = out.decode(errors="ignore").strip().splitlines()
    data = json.loads(lines[-1]) if lines else {}
    if not data.get("title"):
        return None
    return Recognized(title=data["title"], artist=data.get("artist") or "",
                      cover=data.get("cover") or "", url=data.get("url") or "")


async def recognize(src: Path) -> Recognized | None:
    """Пробуем отрывок с начала, затем из середины (в начале бывает тишина или речь)."""
    length = await asyncio.to_thread(_duration, src)
    starts = [0.0]
    if length > 45:
        starts.append(length / 2 - SAMPLE_SECONDS / 2)
    for i, start in enumerate(starts):
        sample = src.with_name(f"sample{i}.wav")
        try:
            await asyncio.to_thread(_sample, src, sample, max(0.0, start))
            found = await _ask_shazam(sample)
            if found:
                return found
        except Exception as e:
            logging.warning("распознавание (отрывок %s) не удалось: %s", i, str(e)[:150])
    return None
