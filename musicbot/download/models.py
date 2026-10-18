"""Данные скачивания: результат поиска и скачанный файл."""

import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_SIZE = 49 * 1024 * 1024  # лимит Telegram для ботов ~50 МБ

_limit = MAX_SIZE


def size_limit() -> int:
    """Наибольший файл, который мы готовы отдать: 49 МБ, а если работает юзербот — USERBOT_MAX_MB."""
    return _limit


def set_size_limit(value: int) -> None:
    global _limit
    _limit = max(MAX_SIZE, value)


def download_cap() -> int:
    """Предел для yt-dlp: с запасом, потому что после перекодирования в mp3 файл бывает меньше."""
    return _limit * 4 if _limit == MAX_SIZE else _limit * 2


@dataclass
class SearchResult:
    id: str
    title: str
    artist: str
    duration: int
    url: str
    thumb_url: str = ""
    source: str = ""  # ytm / ytmv / yt / sc — откуда результат
    verified: bool = False  # совпал с записью каталога (Deezer/iTunes)
    label: str = ""  # тип версии: Original, Remix, Live, Slowed…
    exact: bool = False  # точный трек: то же название и исполнитель без пометок, длительность как в каталоге
    loose: bool = False  # подобрана с мягкой проверкой длины: после скачивания сверяем с её собственной длиной


@dataclass
class Downloaded:
    path: Path
    thumb: Path | None
    title: str
    artist: str
    duration: int
    source: str
    workdir: Path
    origin: str = ""  # ссылка, по которой файл на самом деле скачался (для кэша звука)

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)
