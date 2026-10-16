"""Данные скачивания: результат поиска и скачанный файл."""

import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_SIZE = 49 * 1024 * 1024  # лимит Telegram для ботов ~50 МБ


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


@dataclass
class Downloaded:
    path: Path
    thumb: Path | None
    title: str
    artist: str
    duration: int
    source: str
    workdir: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)
