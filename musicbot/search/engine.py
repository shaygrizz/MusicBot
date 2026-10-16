"""Обычный и глубокий поиск: параллельные источники, дедлайны, сборка выдачи."""

import asyncio
import logging
import os
import time

from ..download import (
    SearchResult,
)
from ..textutil import norm
from .catalogs import canon_relevant, dedupe_canons, deezer, deezer_artist, genius, itunes, yandex
from .exact import is_clean_canon
from .lyrics import KNOWN_RANK, LYRIC_MIN_WORDS, lyric_candidates, respell
from .models import Canon, Outcome, lazy_url
from .ranking import artist_ok, merge
from .sources import (
    finished,
    safe_call,
    search_audius,
    search_hitmo,
    search_soundcloud,
    search_youtube,
    search_ytm,
    search_ytm_top,
)
from .text import clean_query, query_tokens

FAST_DEADLINE = float(os.getenv("SEARCH_FAST_DEADLINE", "3.0"))  # быстрые источники (каталоги, YT Music) ждём не дольше

SOFT_DEADLINE = 2.0  # обычный поиск: медленным источникам (SoundCloud, yt-dlp) дольше не ждём

DEEP_DEADLINE = 7.0  # глубокий поиск


_search_cache: dict[tuple, tuple[float, Outcome]] = {}


CACHE_TTL = 600  # повторный запрос в течение 10 минут отвечает мгновенно


async def search(query: str, limit: int = 8, deep: bool = False, fast_only: bool = False) -> Outcome:
    key = (clean_query(query).lower() or query.strip().lower(), limit, deep, fast_only)
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    out = await _search_impl(query, limit, deep, fast_only)
    if out.results:
        if len(_search_cache) > 200:
            _search_cache.pop(next(iter(_search_cache)))
        _search_cache[key] = (time.time(), out)
    return out


async def _search_impl(query: str, limit: int, deep: bool, fast_only: bool = False) -> Outcome:
    q = clean_query(query) or query.strip()
    words = q.split()
    tokens = query_tokens(q)
    n_cat = 15 if deep else 5  # сколько записей просим у каталогов
    loop = asyncio.get_running_loop()
    start = loop.time()
    T = asyncio.create_task

    # шаг 1: все источники стартуют сразу. Быстрые ждём, медленные — только до мягкого дедлайна
    fast = {
        "top": T(safe_call(search_ytm_top, q)),
        "ytm": T(safe_call(search_ytm, q, "songs", "ytm")),
        "dz": T(safe_call(deezer, q, n_cat)),
        "it": T(safe_call(itunes, q, n_cat)),
        "ym": T(safe_call(yandex, q, n_cat)),
        "art": T(safe_call(deezer_artist, q, 25 if deep else 12)),
    }
    slow = {
        "ytmv": T(safe_call(search_ytm, q, "videos", "ytmv")),
        "yt": T(safe_call(search_youtube, q, 10 if deep else 5)),
        "sc": T(safe_call(search_soundcloud, q, 15 if deep else 8)),
        "au": T(safe_call(search_audius, q)),
        "hm": T(safe_call(search_hitmo, q)),
    }
    if len(words) >= 4:  # похоже на строчку из песни — ищем ещё и «текстовые» ролики
        slow["lyr"] = T(safe_call(search_ytm, q + " lyrics", "videos", "ytmv"))
    if deep:
        slow["gen"] = T(safe_call(genius, q))
        slow["oa"] = T(safe_call(search_ytm, q + " official audio", "videos", "ytmv"))
        slow["lyr2"] = T(safe_call(search_ytm, q + " lyrics", "songs", "ytm"))
    lyric = T(safe_call(lyric_candidates, q)) if len(words) >= LYRIC_MIN_WORDS and not fast_only else None
    # Deezer и проверка «это имя исполнителя?» отвечают первыми — по ним решаем, что делать дальше
    await asyncio.wait([fast["dz"], fast["art"]], timeout=FAST_DEADLINE)

    artist_hit = finished(fast["art"]) or None
    if artist_hit:  # «queen» -> только песни Queen, остальные источники не нужны
        for t in list(fast.values()) + list(slow.values()) + ([lyric] if lyric else []):
            if not t.done():
                t.cancel()
        name, tracks = artist_hit
        results = [
            SearchResult(
                id=f"lz{i}",
                title=c.title,
                artist=c.artist,
                duration=c.duration,
                url=lazy_url(c),
                thumb_url=c.thumb,
                source="top",
                verified=True,
            )
            for i, c in enumerate(tracks)
        ]
        return Outcome(results, "", q, kind="artist", artist=name)

    canons_dz = [c for c in finished(fast["dz"]) if canon_relevant(c, tokens)]

    # шаг 2 стартует сразу, не дожидаясь остальных источников: по точным названиям из каталога
    # ищем скачиваемые версии (то, что не нашлось по сырому запросу)
    second_songs = [T(safe_call(search_ytm, f"{c.artist} {c.title}", "songs", "ytm")) for c in canons_dz[:3]]
    second_top = [T(safe_call(search_ytm_top, f"{c.artist} {c.title}")) for c in canons_dz[:2]]
    group = [t for k, t in fast.items() if k not in ("dz", "art")] + second_songs + second_top
    # один зависший источник (например, Яндекс с другого адреса) не должен тормозить весь поиск
    await asyncio.wait(group, timeout=max(0.3, FAST_DEADLINE - (loop.time() - start)))
    late = [k for k, t in fast.items() if not t.done()]
    for t in group:
        if not t.done():
            t.cancel()

    canons = dedupe_canons(
        [canons_dz, [c for c in finished(fast["it"]) + finished(fast["ym"]) if canon_relevant(c, tokens)]]
    )

    if fast_only:  # подбор версии при нажатии на трек: медленные источники не ждём совсем
        for t in slow.values():
            t.cancel()
        slow = {}
    left = max(0.0, (DEEP_DEADLINE if deep else SOFT_DEADLINE) - (loop.time() - start))
    if left and slow:
        await asyncio.wait(slow.values(), timeout=left)

    # порядок в пуле = приоритет при равных: «Top result», найденное по каталогу, затем всё остальное
    pool: list[SearchResult] = list(finished(fast["top"]))
    for t in second_top + second_songs:
        pool.extend(finished(t))
    pool.extend(finished(fast["ytm"]))
    extra_canons: list[Canon] = []
    for key, t in slow.items():
        if not t.done():
            t.cancel()  # поток дождётся своего таймаута, результат нам уже не нужен
            continue
        if key == "gen":
            extra_canons = [c for c in finished(t) if canon_relevant(c, tokens)]
        else:
            pool.extend(finished(t))
    canons = dedupe_canons([canons, extra_canons])
    late += [k for k, t in slow.items() if not t.done() or t.cancelled()]
    logging.info("поиск %r: %.1f с, не успели: %s", q, loop.time() - start, ", ".join(late) or "никто")

    if deep:
        results = merge(pool, canons, q, 14, strict=False)
    else:
        results = merge(pool, canons, q, limit)
        if len(results) < 3:  # строгий фильтр оставил мало — ослабляем (например, строчка из песни)
            results = merge(pool, canons, q, limit, strict=False)
    # «исполнитель + название» в запросе — это не строчка из песни, поиск по тексту только внесёт шум
    named = any(norm(c.artist) in norm(q) and norm(c.title) in norm(q) for c in canons_dz)
    short_title = bool(canons_dz) and len(words) < 5  # короткий запрос, по названию нашлось: это название, а не текст
    # запрос мог быть строчкой из песни, а не названием: Genius ищет по словам из текста
    if lyric and not named and not short_title:
        await asyncio.wait([lyric], timeout=max(1.5, FAST_DEADLINE - (loop.time() - start)))
        by_text = finished(lyric)
        if not by_text:  # ничего не нашлось — возможно, в запросе опечатки: правим их по найденным названиям
            fixed = respell(q, [f"{r.artist} {r.title}" for r in pool] + [f"{c.artist} {c.title}" for c in canons])
            if fixed.lower() != q.lower():
                try:
                    by_text = await asyncio.wait_for(asyncio.to_thread(lyric_candidates, fixed, True), 4.0)
                    logging.info("поиск по тексту: опечатки %r -> %r", q, fixed)
                except Exception as e:
                    logging.info("поиск по тексту с исправлением не удался: %s", str(e)[:80])
        if by_text:
            top = [
                SearchResult(
                    id=f"ly{i}",
                    title=c.title,
                    artist=c.artist,
                    duration=c.duration,
                    url=lazy_url(c),
                    thumb_url=c.thumb,
                    source="top",
                    verified=True,
                )
                for i, c in enumerate(by_text)
            ]
            rest = [r for r in results if not any(norm(r.title) == norm(c.title) and artist_ok(r, c) for c in by_text)]
            # по названию ничего не нашлось и песня известная -> первая; иначе (чужие загрузки, малоизвестная) -> второй
            at = 0 if not canons_dz and by_text[0].rank >= KNOWN_RANK else min(1, len(rest))  # неизвестную — второй
            results = (rest[:at] + top + rest[at:])[: 14 if deep else limit]
            logging.info("поиск по тексту %r: %s", q, "; ".join(f"{c.artist} - {c.title}" for c in by_text))
    if lyric and not lyric.done():
        lyric.cancel()
    hint = ""
    if not results and canons:
        # скачиваемого не нашлось (источники медленные или закрыты), но песня есть в каталоге: источник подберётся при нажатии
        clean = [c for c in canons if is_clean_canon(c)] or canons
        results = [
            SearchResult(
                id=f"cn{i}",
                title=c.title,
                artist=c.artist,
                duration=c.duration,
                url=lazy_url(c),
                thumb_url=c.thumb,
                source="top",
                verified=True,
            )
            for i, c in enumerate(clean[:3])
        ]
        hint = f"{canons[0].artist} — {canons[0].title}"
    return Outcome(results, hint, q)
