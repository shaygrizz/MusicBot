"""Подбор версий и скачивание с автоповтором."""

import asyncio
import logging
import re
from dataclasses import replace

from .. import audiocache
from ..download import (
    TOO_BIG,
    Downloaded,
    SearchResult,
    download,
    probe_duration,
)
from ..paths import DOWNLOAD_DIR
from ..textutil import BAD_VERSION_RE, core_title, flat, is_mix_cut, latin, norm, same_artist
from .engine import search
from .models import is_lazy

ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|/|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b|\bи\b)\s*", re.I)
STRICT_SECONDS = 8  # обычная разница длины одной и той же записи на разных площадках
LOOSE_SECONDS, LOOSE_SHARE = 20, 0.12  # последний шаг: радиоверсия против альбомной и т.п.
MIN_TITLE_ONLY = 5  # поиск по одному названию — только для названий не короче (иначе слишком много чужих песен)


def artist_names(artist: str) -> list[str]:
    """«Gorillaz, Del The Funky Homosapien» -> ["Gorillaz", "Del The Funky Homosapien"]."""
    return [a.strip() for a in ARTIST_SPLIT_RE.split(artist or "") if norm(a)]


def _artist_matches(names: list[str], r: SearchResult) -> bool:
    """Исполнитель совпадает целым именем, а не отдельным словом: иначе «the» из «Del The Funky Homosapien»
    подошло бы почти к любой песне. Имя может стоять в названии ролика («Sabrina Carpenter - Manchild (lyrics)»)
    и быть в другой азбуке («Kino» и «Кино»)."""
    text = norm(f"{r.artist} {r.title}")
    theirs = artist_names(r.artist)
    for name in names:
        n = norm(name)
        if len(n) < 4:  # «DDT», «U2», «MØ»: короткое имя ищем только среди исполнителей, не в тексте
            if any(n == norm(x) for x in theirs) or any(same_artist(name, x) for x in theirs):
                return True
        elif n in text or any(same_artist(name, x) for x in theirs):
            return True
    return False


def reject_reason(meta: SearchResult, r: SearchResult, loose: bool = False) -> str | None:
    """Почему версия не подходит (None — подходит). Каждое правило строгое; loose ослабляет только длину."""
    title = core_title(meta.title)
    want = norm(title)
    # «Vienna - Live at Nassau Coliseum…» в каталоге и «Vienna (Live at Nassau Coliseum…)» на YouTube — одна запись:
    # третье сравнение не выбрасывает скобки, поэтому версия должна совпасть целиком (Astronomyy Remix ≠ Blackbear Remix)
    if want and want not in norm(r.title) and latin(title) not in latin(r.title) and flat(title) not in flat(r.title):
        return "другое название"
    names = artist_names(meta.artist)
    if names and not _artist_matches(names, r):
        return "другой исполнитель"
    if not BAD_VERSION_RE.search(meta.title) and BAD_VERSION_RE.search(r.title):
        return "ремикс, кавер или другая обработка"
    if meta.duration and r.duration:
        delta = abs(r.duration - meta.duration)
        allowed = max(LOOSE_SECONDS, int(meta.duration * LOOSE_SHARE)) if loose else STRICT_SECONDS
        if delta > allowed:
            return f"длина {r.duration} с вместо {meta.duration} с"
    return None


async def resolve(
    meta: SearchResult, exclude: set[str] | None = None, limit: int = 4, why: list | None = None
) -> list[SearchResult]:
    """Скачиваемые версии нужного трека, лучшие первыми. Ищет от точного к широкому и останавливается,
    как только нашлась подходящая версия:
      1. главный исполнитель + название («Gorillaz Clint Eastwood»: с гостями в запросе YouTube Music
         не показывает альбомную версию);
      2. все исполнители + название;
      3. одно название (исполнитель на площадке записан иначе), проверка исполнителя остаётся строгой;
      4. полный поиск со всеми источниками;
      5. те же результаты с мягкой проверкой длины (радиоверсия против альбомной); такие версии помечены loose,
         и после скачивания длина файла сверяется с длиной самой версии.
    В why (если передан) складываются лучшие отвергнутые версии с причиной — для диагностики."""
    exclude = exclude or set()
    if is_mix_cut(meta.title):  # длина «- Mixed» в каталоге — длина куска в миксе, сравнивать её нельзя
        meta = replace(meta, duration=0)
    title = core_title(meta.title)  # «Draw You Out - From The Hunger Games…» ищем и сравниваем как «Draw You Out»
    names = artist_names(meta.artist)
    main = names[0] if names else ""
    queries = [f"{main} {title}", f"{meta.artist} {title}"]
    if len(norm(title)) >= MIN_TITLE_ONLY:
        queries.append(title)
    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
    pool: dict[str, SearchResult] = {}

    def usable(r: SearchResult) -> bool:
        return r.url not in exclude and not is_lazy(r.url)

    def strict() -> list[SearchResult]:
        good = [r for r in pool.values() if reject_reason(meta, r) is None]
        good.sort(key=lambda r: abs(r.duration - meta.duration) if (meta.duration and r.duration) else 999)
        return good[:limit]

    partial: list[str] = []  # запросы, на которые YouTube Music не успел ответить (выдача неполная)

    async def collect(query: str, fast: bool, fresh: bool = False) -> None:
        out = await search(query, 10, fast_only=fast, fresh=fresh)
        if out.partial and fast and not fresh:
            partial.append(query)
        for r in out.results:
            if usable(r):
                pool.setdefault(r.url, r)

    for query in queries:
        await collect(query, fast=True)
        if good := strict():
            return good
    await collect(queries[0], fast=False)  # медленные источники: yt-dlp, SoundCloud
    if good := strict():
        return good
    # выдача была неполной из-за медленного ответа, а не потому, что песни нет: повторяем мимо кэша
    for query in partial[:2]:
        await collect(query, fast=True, fresh=True)
        if good := strict():
            return good
    loose = [replace(r, loose=True) for r in pool.values() if reject_reason(meta, r, loose=True) is None]
    loose.sort(key=lambda r: abs(r.duration - meta.duration) if (meta.duration and r.duration) else 999)
    if loose:
        logging.info(
            "версия для %s - %s найдена с мягкой проверкой длины: %s с", meta.artist, meta.title, loose[0].duration
        )
        return loose[:limit]
    # ничего не подошло: пишем, что было в выдаче и почему не подошло, — по логу видно причину
    tried = sorted(pool.values(), key=lambda r: reject_reason(meta, r) == "другое название")[:3]  # сначала близкие
    reasons = [(r, reject_reason(meta, r) or "") for r in tried]
    if why is not None:
        why.extend(reasons)
    logging.info(
        "версия не найдена: %s - %s (%s с); %s",
        meta.artist,
        meta.title,
        meta.duration,
        "; ".join(f"{r.artist} - {r.title} ({r.duration} с): {reason}" for r, reason in reasons) or "выдача пуста",
    )
    return []


DURATION_GUARD = 20  # секунд: на столько настоящая длина файла может отличаться от известной длительности песни


async def _wrong_length(d, meta: SearchResult) -> bool:
    if not meta.duration:
        return False
    real = await asyncio.to_thread(probe_duration, d.path)
    return bool(real) and abs(real - meta.duration) > DURATION_GUARD


async def fetch_audio(url: str, meta: SearchResult | None) -> "Downloaded":
    """Скачивает трек. Сначала смотрит общий дисковый кэш: песня, которую уже кто-то скачивал, отдаётся сразу."""
    hit = await asyncio.to_thread(audiocache.get, url, DOWNLOAD_DIR)
    if hit is not None:
        if meta and meta.title:
            hit.title, hit.artist = meta.title, meta.artist or hit.artist
        return hit
    d = await _fetch_audio(url, meta)
    await asyncio.to_thread(audiocache.put, [url, d.origin], d)
    return d


async def _fetch_audio(url: str, meta: SearchResult | None) -> "Downloaded":
    """Не получилось по выбранной ссылке — пробует другие версии того же трека,
    и только потом запасные источники (SoundCloud, dydki). Ссылка без метаданных качается как есть."""
    if meta is None or not meta.title:
        return await download(url, "audio", meta)
    if is_mix_cut(meta.title):
        # «Monument [True Electric] - Mixed»: в каталоге длина куска из микса, на YouTube та же запись целиком.
        # Длину не сравниваем, совпасть по-прежнему должны название и исполнитель
        meta = replace(meta, duration=0)

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
            # скачалась другая песня: отбрасываем, а не подписываем чужое правильным названием.
            # Версию, принятую с мягкой проверкой длины, сверяем с её собственной длиной, а не с каталогом
            if await _wrong_length(d, cand if cand.loose else meta):
                logging.info("версия отброшена: длина файла не совпадает с %s с (%s)", meta.duration, cand.url)
                last = RuntimeError("длительность файла не совпадает с песней")
                d.cleanup()
                continue
            d.title, d.artist = meta.title, meta.artist or d.artist  # название и исполнитель из каталога
            d.origin = cand.url  # под этой ссылкой файл тоже попадёт в кэш
            return d
        if not first_round:
            break
        first_round = False
        candidates = await resolve(meta, tried, 3)
        if not candidates:
            break
    try:  # запасные источники ищут по названию: хвост каталога («- From …», «+ …») им только мешает
        d = await download("title://", "audio", replace(meta, title=core_title(meta.title)))
        if await _wrong_length(d, meta):
            d.cleanup()
            raise RuntimeError("длительность файла не совпадает с песней")
        d.title, d.artist = meta.title, meta.artist or d.artist
        return d
    except Exception as e:
        raise last or e
