"""Точный трек: тот же оригинал, без ремиксов, live, каверов и обрезков."""

import re
from difflib import SequenceMatcher

from ..download import (
    SearchResult,
)
from ..textutil import BAD_VERSION_RE, norm
from .models import Canon

_PAREN_RE = re.compile(r"[(\[]([^)\]]*)[)\]]")

# пометки в скобках, которые трек не меняют (feat., official audio, remastered, from "…", год)
_SAFE_PAREN_RE = re.compile(
    r"^\s*(feat\b|ft\b|featuring|with\b|prod\b|official|audio|video|lyric|remaster|from\b|visuali[sz]er|"
    r"hd\b|hq\b|4k|explicit|clean|music|topic|\d{4}\b)",
    re.I,
)


_NOISE_TAIL_RE = re.compile(
    r"\s[-–|]\s*(official|audio|video|lyrics?|music video|hd|hq|4k|visuali[sz]er|topic)\b.*$", re.I
)


_FEAT_SPLIT_RE = re.compile(r"\s(?:feat\.?|ft\.?|featuring|with)\s", re.I)

_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b)\s*", re.I)

EXACT_DURATION = 8  # секунд: во столько длительность точного трека может отличаться от каталожной


def main_artist(artist: str) -> str:
    return _ARTIST_SPLIT_RE.split(artist.strip())[0].strip()


def core_title(title: str, artist: str = "") -> tuple[str, list[str]]:
    """Название без «Artist - », feat. и пометок + список пометок из скобок."""
    t = _NOISE_TAIL_RE.sub("", title)
    if artist:
        t = re.sub(rf"^\s*{re.escape(artist)}\b[^-–:]*?\s*[-–:]\s*", "", t, flags=re.I)
    quals = _PAREN_RE.findall(t)
    t = _FEAT_SPLIT_RE.split(_PAREN_RE.sub(" ", t))[0]
    return norm(t), quals


def artist_names(artist: str) -> set[str]:
    out = set()
    for part in _ARTIST_SPLIT_RE.split(artist):
        n = norm(re.sub(r"\b(topic|vevo|official)\b", " ", part, flags=re.I))
        if n:
            out.add(n)
            out.add(n.removeprefix("the"))
    return out


def is_clean_canon(c: Canon) -> bool:
    """Запись каталога без пометок (Single Version, Live, Acapella…): только такие считаем эталоном."""
    return not BAD_VERSION_RE.search(c.title) and all(_SAFE_PAREN_RE.match(q) for q in core_title(c.title)[1])


def is_exact(r: SearchResult, c: Canon) -> bool:
    """Это тот самый трек из каталога: название и исполнитель те же, без ремикса/live/кавера/версий,
    длительность как у оригинала (не отрывок и не удлинённая версия)."""
    main = main_artist(c.artist)
    ct, _ = core_title(c.title)
    rt, quals = core_title(r.title, main)
    if not ct or rt != ct:
        return False
    if not all(_SAFE_PAREN_RE.match(q) for q in quals):
        return False
    if BAD_VERSION_RE.search(r.title):
        return False
    want = artist_names(main)
    have = artist_names(r.artist) if r.artist else set()
    prefixed = bool(re.match(rf"^\s*{re.escape(main)}\b", r.title, re.I))  # «Artist - Title» в названии ролика
    if not (want & have or prefixed):
        return False  # «The Retro Weeknd» — не The Weeknd
    if c.duration and r.duration:
        return abs(r.duration - c.duration) <= EXACT_DURATION
    if c.duration and not r.duration:
        return r.source in ("ytm", "top")  # у официальных «песен» YouTube Music длительность иногда не отдаётся
    return True


def mark_exact(r: SearchResult, refs: list[Canon]) -> None:
    for c in refs:
        if is_exact(r, c):
            r.exact = True
            if not r.duration:
                # длительность неизвестна — берём каталожную, чтобы скачивание могло отбраковать обрезанный файл
                r.duration = c.duration
            return
    r.exact = False


ARTIST_TYPO_RATIO = 0.85  # опечатка в имени исполнителя внутри запроса («metalica nothing else matters»)


def artist_in_query(names: set[str], query: str) -> bool:
    """Имя исполнителя названо в запросе вместе с другими словами; в слове допустима опечатка (длинные имена)."""
    q = norm(query)
    words = [norm(w) for w in query.split()]
    for n in names:
        if not n or len(q) <= len(n) + 1:
            continue
        if n in q or (
            len(n) >= 5 and any(len(w) >= 4 and SequenceMatcher(None, n, w).ratio() >= ARTIST_TYPO_RATIO for w in words)
        ):
            return True
    return False


def exact_refs(canons: list[Canon], query: str) -> list[Canon]:
    """Эталон для проверки «точности»: самая популярная чистая запись каталога (каталог отдаёт её первой)
    и её дубли с той же длительностью. Другие песни с таким названием и версии другой длины эталоном не служат."""
    clean = [c for c in canons if is_clean_canon(c)]
    if not clean:
        return []
    # исполнитель назван, если его имя есть в запросе вместе с другими словами (запрос «starboy» — это название песни,
    # а не исполнитель Starboy)
    named = [c for c in clean if artist_in_query(artist_names(main_artist(c.artist)), query)]
    pool = named or clean
    top = pool[0]
    who = artist_names(main_artist(top.artist))
    return [
        c
        for c in pool
        if core_title(c.title)[0] == core_title(top.title)[0]
        and artist_names(main_artist(c.artist)) & who
        and (not top.duration or not c.duration or abs(c.duration - top.duration) <= EXACT_DURATION)
    ]
