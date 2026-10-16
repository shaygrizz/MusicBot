"""Сверка результатов с каталогом, ранжирование, склейка дублей."""

import re

from ..download import (
    SearchResult,
)
from ..textutil import BAD_VERSION_RE, norm
from .exact import exact_refs, mark_exact
from .models import Canon
from .text import query_tokens

DEDUP_SECONDS = 3

AU_RESERVE = 2  # сколько мест в списке всегда оставляем ремиксам Audius

STRONG_DUR = 3  # разница длительности (с), при которой считаем запись «той самой»

OFFICIAL_DUR = 6  # то же для «официальных» источников (YouTube Music, VK), где длительность надёжнее

# приоритет источника: меньше — выше в списке
PRIORITY = {"verified": 0, "top": 1, "ytm": 1, "sc": 2, "ytmv": 3, "yt": 4, "hm": 4, "au": 4, "bad": 5}


def artist_ok(r: SearchResult, c: Canon) -> bool:
    text = norm(f"{r.artist} {r.title}")
    toks = [t for t in re.split(r"[\s,&]+", c.artist.lower()) if len(norm(t)) >= 3]
    return not toks or any(norm(t) in text for t in toks)


def verify(r: SearchResult, canons: list[Canon]) -> Canon | None:
    nt = norm(r.title)
    official = r.source in ("ytm", "top")
    for c in canons:
        ct = norm(c.title)
        if not ct or ct not in nt or not artist_ok(r, c):
            continue
        if r.duration and c.duration:
            if abs(r.duration - c.duration) > (OFFICIAL_DUR if official else STRONG_DUR):
                continue
        elif not official:
            continue  # без длительности подтверждаем только официальные источники
        return c
    return None


def rank(r: SearchResult, query: str) -> int:
    if r.exact:  # точные треки всегда впереди: внутри — по источнику (официальные «песни» YouTube Music первыми)
        return PRIORITY[r.source or "yt"]
    if BAD_VERSION_RE.search(r.title) and not BAD_VERSION_RE.search(query):
        return PRIORITY["bad"] + 10  # каверы, ремиксы, slowed — только если их просили
    if r.verified:
        return PRIORITY["verified"] + 10
    return PRIORITY[r.source or "yt"] + 10


def is_relevant(
    r: SearchResult, tokens: list[str], strict: bool, must: list[str] | None = None, ref_dur: int = 0
) -> bool:
    """Результат без совпадения с каталогом должен пересекаться с запросом по словам.
    Обычный поиск требует большую часть слов (чужие «LOSER» не нужны), глубокий — хотя бы одно."""
    if r.verified or not tokens:
        return True
    text = norm(f"{r.artist} {r.title}")
    if not strict:
        return any(t in text for t in tokens if len(t) >= 3)
    hit = sum(1 for t in tokens if t in text)
    need = 1 if len(tokens) == 1 else max(2, -(-len(tokens) * 3 // 5))  # ceil(60%), минимум два слова
    if hit < need:
        return False
    if must and not any(t in text for t in must):
        return False  # нужна песня, а не «другие песни того же исполнителя»
    if ref_dur and r.duration and not (0.6 * ref_dur <= r.duration <= 1.6 * ref_dur):
        return False  # клипы по минуте и часовые подборки — не то
    return True


def same_track(a: SearchResult, b: SearchResult) -> bool:
    if norm(a.title) != norm(b.title):
        return False
    if a.duration and b.duration:
        return abs(a.duration - b.duration) <= DEDUP_SECONDS
    return norm(a.artist) == norm(b.artist)  # длительность неизвестна — сравниваем по исполнителю


def merge(
    pool: list[SearchResult], canons: list[Canon], query: str, limit: int, strict: bool = True
) -> list[SearchResult]:
    for r in pool:
        c = verify(r, canons)
        if c:  # нашли совпадение в каталоге — подставляем чистые название, исполнителя, обложку
            r.verified = True
            r.title, r.artist = c.title, c.artist
            if c.thumb and not r.thumb_url:
                r.thumb_url = c.thumb
    refs = exact_refs(canons, query)
    for r in pool:
        mark_exact(r, refs)
    tokens = query_tokens(query)
    must, ref_dur = [], 0
    if canons and strict:  # по лучшей записи каталога понимаем, какие слова — название, и сколько оно длится
        must = [t for t in tokens if t not in norm(canons[0].artist)]
        ref_dur = canons[0].duration
    seen_urls, kept = set(), []
    for r in sorted(pool, key=lambda r: rank(r, query)):  # sorted стабильна: порядок источника сохраняется
        if r.url in seen_urls or not is_relevant(r, tokens, strict, must, ref_dur):
            continue
        if any(same_track(k, r) for k in kept):
            continue
        seen_urls.add(r.url)
        kept.append(r)
    # ремиксы Audius в общем рейтинге всегда внизу и вытеснились бы — оставляем им до двух мест в конце
    au = [r for r in kept if r.source == "au"][:AU_RESERVE]
    others = [r for r in kept if r.source != "au"]
    return others[: max(0, limit - len(au))] + au
