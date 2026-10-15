"""Запускается отдельным процессом в окружении Python 3.12 (.venv312), где есть готовая сборка ShazamIO.
Использование: python shazam_worker.py <путь к wav>. Печатает одну строку JSON."""
import asyncio
import json
import sys

from shazamio import Shazam


async def main(path: str) -> None:
    shazam = Shazam()
    call = getattr(shazam, "recognize", None) or shazam.recognize_song  # название метода менялось между версиями
    result = await call(path)
    track = (result or {}).get("track") or {}
    print(json.dumps({
        "title": track.get("title"),
        "artist": track.get("subtitle"),
        "cover": (track.get("images") or {}).get("coverart", ""),
        "url": track.get("url", ""),
    }, ensure_ascii=False))


asyncio.run(main(sys.argv[1]))
