"""Данные поиска: запись каталога, итог поиска, ссылки-заглушки."""

import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..download import (
    SearchResult,
)

ICONS = {"top": "🎵", "ytm": "🎵", "ytmv": "🎬", "yt": "▶️", "sc": "☁️", "hm": "💿", "au": "🎧"}


@dataclass
class Canon:
    """Запись из каталога (Deezer/iTunes/Genius): точное название, исполнитель, длительность, обложка."""

    artist: str
    title: str
    duration: int
    thumb: str = ""
    rank: int = 0  # популярность в Deezer (больше — известнее); только для выбора среди кандидатов


@dataclass
class Outcome:
    results: list[SearchResult] = field(default_factory=list)
    hint: str = ""  # что нашли в каталоге, даже если скачать нечего
    query: str = ""  # очищенный запрос — нужен, чтобы продолжить «глубоким» поиском
    kind: str = ""  # "artist" — запрос был именем исполнителя, показаны только его песни
    artist: str = ""


def lazy_url(c: Canon) -> str:
    """Песня из каталога без ссылки на скачивание: источник подберётся в момент нажатия."""
    return "lazy://" + urllib.parse.quote(f"{c.artist}|{c.title}|{c.duration}", safe="")


def lazy_for(artist: str, title: str, duration: int = 0) -> str:
    """Ссылка-заглушка для песни, у которой нет адреса скачивания (избранное): источник подберётся при воспроизведении."""
    return lazy_url(Canon(artist, title, duration))


def is_lazy(url: str) -> bool:
    return url.startswith("lazy://")
