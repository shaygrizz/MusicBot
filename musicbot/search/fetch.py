"""Подбор версий и скачивание с автоповтором."""

import asyncio
import logging
import re

from ..download import (
    TOO_BIG,
    Downloaded,
    SearchResult,
    download,
    probe_duration,
)
from ..textutil import BAD_VERSION_RE, norm
from .engine import search
from .models import is_lazy


async def resolve(meta: SearchResult, exclude: set[str] | None = None, limit: int = 4) -> list[SearchResult]:
    """Скачиваемые версии нужного трека (точное название, близкая длительность), лучшие первыми."""
    exclude = exclude or set()
    query = f"{meta.artist} {meta.title}".strip()
    out = await search(query, 10, fast_only=True)  # быстро: YouTube Music + каталог
    if not [r for r in out.results if r.url not in exclude and not is_lazy(r.url)]:
        out = await search(query, 10)  # быстрых источников не хватило — полный поиск
    want = norm(meta.title)
    asked_bad = bool(BAD_VERSION_RE.search(meta.title))
    artist_tokens = [norm(t) for t in re.split(r"[\s,&]+", meta.artist) if len(norm(t)) >= 3]

    def usable(r: SearchResult) -> bool:
        return r.url not in exclude and not is_lazy(r.url)

    def exact(r: SearchResult) -> bool:
        if want and want not in norm(r.title):
            return False
        # то же название у другого исполнителя («The Way I Still Love You» вместо «still love you») — чужая песня
        if artist_tokens and not any(t in norm(f"{r.artist} {r.title}") for t in artist_tokens):
            return False
        if meta.duration and r.duration and abs(r.duration - meta.duration) > 8:
            return False
        return asked_bad or not BAD_VERSION_RE.search(r.title)

    good = [r for r in out.results if usable(r) and exact(r)]
    # ближайшие по длительности первыми (сортировка стабильна: при равенстве остаётся порядок источников)
    good.sort(key=lambda r: abs(r.duration - meta.duration) if (meta.duration and r.duration) else 999)
    return good[:limit]  # подходящих нет — пусто: лучше сообщить об ошибке, чем играть чужую песню


DURATION_GUARD = 20  # секунд: на столько настоящая длина файла может отличаться от известной длительности песни


async def _wrong_length(d, meta: SearchResult) -> bool:
    if not meta.duration:
        return False
    real = await asyncio.to_thread(probe_duration, d.path)
    return bool(real) and abs(real - meta.duration) > DURATION_GUARD


async def fetch_audio(url: str, meta: SearchResult | None) -> "Downloaded":
    """Скачивает трек. Не получилось по выбранной ссылке — пробует другие версии того же трека,
    и только потом запасные источники (SoundCloud, dydki). Ссылка без метаданных качается как есть."""
    if meta is None or not meta.title:
        return await download(url, "audio", meta)

    tried: set[str] = set()
    last: Exception | None = None
    candidates: list[SearchResult] = []
    if not is_lazy(url):
        candidates.append(
            SearchResult(
                id="",
                title=meta.title,
                artist=meta.artist,
                duration=meta.duration,
                url=url,
                thumb_url=meta.thumb_url,
                source=meta.source,
            )
        )
    first_round = True
    while True:
        for cand in candidates:
            tried.add(cand.url)
            try:
                d = await download(cand.url, "audio", cand if cand.url != url else meta, fallbacks=False)
            except Exception as e:
                if TOO_BIG in str(e):
                    raise
                last = e
                logging.info("версия не скачалась (%s): %s", cand.url, str(e)[:120])
                continue
            # скачалась другая песня: отбрасываем, а не подписываем чужое правильным названием
            if await _wrong_length(d, meta):
                logging.info("версия отброшена: длина файла не совпадает с %s с (%s)", meta.duration, cand.url)
                last = RuntimeError("длительность файла не совпадает с песней")
                d.cleanup()
                continue
            d.title, d.artist = meta.title, meta.artist or d.artist  # название и исполнитель из каталога
            return d
        if not first_round:
            break
        first_round = False
        candidates = await resolve(meta, tried, 3)
        if not candidates:
            break
    try:  # запасные источники по названию
        d = await download("title://", "audio", meta)
        if await _wrong_length(d, meta):
            d.cleanup()
            raise RuntimeError("длительность файла не совпадает с песней")
        d.title, d.artist = meta.title, meta.artist or d.artist
        return d
    except Exception as e:
        raise last or e
