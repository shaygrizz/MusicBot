"""Поиск песни по словам из текста (Genius) и исправление опечаток."""

import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher, get_close_matches

from ..textutil import BAD_VERSION_RE, norm
from .http import get_json
from .models import Canon

LYRIC_MIN_WORDS = 3  # от скольких слов запрос считаем «строчкой из песни»

KNOWN_RANK = 100_000  # рейтинг Deezer, с которого песню считаем известной (у малоизвестных — ноль или единицы тысяч)

LYRIC_CANDIDATES = 8  # сколько песен с подходящей строкой рассматриваем; берём самую известную


def clean_genius_title(title: str) -> str:
    """«Мама, я танцую (Mom, I Dance)» -> «Мама, я танцую»: английский перевод в скобках не нужен."""
    main, _, tail = title.partition(" (")
    if re.search("[а-яё]", main, re.I) and tail and not re.search("[а-яё]", tail, re.I):
        return main.strip()
    return title.strip()


def sings_it(hit: dict, q: str, fuzzy: bool = False) -> bool:
    """В тексте песни есть строка, почти совпадающая с запросом. Если запрос лишь часть длинной строки
    («Ik voel me tame impala the less I know the better»), песня его только упоминает, а не поёт."""
    want = norm(q)
    if len(want) < 8:
        return False
    for hl in hit.get("highlights") or []:
        for line in str(hl.get("value", "")).splitlines():
            ln = norm(line)
            if len(ln) < 8:
                continue
            if want in ln and len(want) >= 0.8 * len(ln):
                return True
            # опечатки в запросе: строка похожа на запрос по буквам и близка к нему по длине
            if fuzzy and 0.8 <= len(want) / len(ln) <= 1.25 and SequenceMatcher(None, want, ln).ratio() >= 0.85:
                return True
            if ln in want and len(ln) >= 12:  # запрос захватывает несколько строк
                return True
    return False


def _lyric_catalog(q: str, fuzzy: bool = False) -> list[Canon]:
    """Поиск песни по словам из текста (Genius ищет по тексту, а не только по названию).
    «is this the real life is this just fantasy» -> Queen — Bohemian Rhapsody.
    Берём разные песни (каверы и ремастеры с тем же названием отбрасываем), длительность и обложку уточняем в Deezer."""
    data = get_json("https://genius.com/api/search/lyric?per_page=10&q=" + urllib.parse.quote(q))
    out: list[Canon] = []
    for sec in data.get("response", {}).get("sections", []):
        for h in sec.get("hits", []):
            r = h.get("result", {})
            artist = (r.get("primary_artist") or {}).get("name", "")
            title = clean_genius_title(r.get("title", ""))
            if not artist or not title or "Genius" in artist:  # страницы переводов
                continue
            if not sings_it(h, q, fuzzy):
                continue
            if BAD_VERSION_RE.search(title) and not BAD_VERSION_RE.search(q):
                continue
            if any(norm(title) == norm(c.title) and norm(artist) == norm(c.artist) for c in out):
                continue  # ремастеры и повторы одной песни
            out.append(Canon(artist, title, 0, r.get("song_art_image_thumbnail_url", "")))
            if len(out) >= LYRIC_CANDIDATES:
                return out
    return out


def _enrich(c: Canon) -> Canon:
    """Длительность, обложка и известность из Deezer: по длительности проверяется, что скачалась именно эта песня."""
    try:
        want_t, want_a = norm(c.title), norm(c.artist)
        data = get_json("https://api.deezer.com/search?limit=5&q=" + urllib.parse.quote(f"{c.artist} {c.title}"))
        for x in data.get("data", []):
            name = x["artist"]["name"]
            if norm(x["title"]) == want_t and (want_a in norm(name) or norm(name) in want_a):
                return Canon(
                    name,
                    x["title"],
                    int(x.get("duration") or 0),
                    (x.get("album") or {}).get("cover_big", "") or c.thumb,
                    int(x.get("rank") or 0),
                )
    except Exception:
        pass
    return c


def respell(q: str, titles: list[str]) -> str:
    """Исправляет опечатки по словам из найденных названий («fireworc» -> «firework»): в них обычно верное написание."""
    vocab = {w for t in titles for w in re.findall(r"[\w']+", t.lower()) if len(w) >= 3}
    fixed = []
    for w in q.split():
        lw = w.lower().strip("'\"")
        if len(lw) < 3 or lw in vocab:
            fixed.append(w)
            continue
        close = get_close_matches(lw, vocab, n=1, cutoff=0.75)
        fixed.append(close[0] if close else w)
    return " ".join(fixed)


def lyric_candidates(q: str, fuzzy: bool = False) -> list[Canon]:
    """Песня по строчке из текста. Среди подходящих по тексту берём самую известную: при опечатках в запросе
    Genius отдаёт много песен с похожей строкой, и первая из них не обязательно нужная (берём первую известную)."""
    found = _lyric_catalog(q, fuzzy)
    if not found:
        return []
    with ThreadPoolExecutor(len(found)) as ex:
        rich = list(ex.map(_enrich, found))
    # песни нет в Deezer — это не песня («New Music Friday» от Spotify, подборки, страницы Genius): отбрасываем
    rich = [c for c in rich if c.rank > 0]
    if not rich:
        return []
    known = [c for c in rich if c.rank >= KNOWN_RANK]  # порядок Genius сохраняем: он лучше отражает совпадение текста
    if fuzzy:  # запрос с исправленными опечатками — это догадка, поэтому берём только известные песни
        return known[:1]
    return [(known or rich)[0]]
