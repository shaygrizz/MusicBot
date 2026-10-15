"""Где лежат данные. Без DATA_DIR всё остаётся рядом с кодом, как на домашнем компьютере.
На хостинге DATA_DIR=/data указывает на постоянное хранилище, которое переживает пересборку."""
import os
from pathlib import Path

_root = os.getenv("DATA_DIR", "").strip()

if _root:
    DATA_DIR = Path(_root)
    DB = DATA_DIR / "musicbot.db"
    AVATARS = DATA_DIR / "avatars"
    FAVORITES = DATA_DIR / "favorites"
    COOKIES = DATA_DIR / "cookies.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    _here = Path(__file__).parent
    DB = Path("musicbot.db")
    AVATARS = _here / "data" / "avatars"
    FAVORITES = _here / "data" / "favorites"
    COOKIES = Path("cookies.txt")
