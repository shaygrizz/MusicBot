"""Оригинал и версии песни: ремиксы, live, slowed, sped up, acoustic, instrumental, каверы."""

import asyncio
import re

from ..download import (
    SearchResult,
)
from ..textutil import norm
from .catalogs import deezer
from .exact import artist_names, core_title, is_clean_canon, main_artist, mark_exact
from .models import Canon, Outcome
from .ranking import PRIORITY, same_track
from .sources import safe_call, search_audius, search_soundcloud, search_ytm, search_ytm_top

VERSION_KINDS = [
    ("Remix", re.compile(r"\b(remix|rmx|bootleg|rework|flip|(?<!radio )edit|mashup)\b", re.I)),
    ("Live", re.compile(r"\b(live|concert|session|unplugged|tour)\b", re.I)),
    ("Sped up", re.compile(r"\b(sped\s*up|speed\s*up|nightcore)\b", re.I)),  # раньше Slowed: «sped up + reverb»
    ("Slowed", re.compile(r"\b(slowed|reverb)\b", re.I)),
    ("Acoustic", re.compile(r"\b(acoustic|piano version|stripped)\b", re.I)),
    ("Instrumental", re.compile(r"\b(instrumental|karaoke|backing track)\b", re.I)),
    ("Cover", re.compile(r"\b(covers?|tribute|кавер)\b", re.I)),
]


# дополнительные виды для полного поиска («все варианты песни»)
EXTRA_KINDS = [
    ("8D", re.compile(r"\b(8d|16d|3d audio)\b", re.I)),
    ("Bass boosted", re.compile(r"\b(bass\s*boosted?|bassboost|phonk)\b", re.I)),
    ("Extended", re.compile(r"\b(extended|full (?:version|song)|long version|club mix)\b", re.I)),
    ("Short", re.compile(r"\b(short|radio edit|snippet|cut)\b", re.I)),
    ("Lyrics", re.compile(r"\b(lyrics?|lyric video|текст|с текстом)\b", re.I)),
]

KIND_ORDER = [k for k, _ in VERSION_KINDS]
# порядок показа: сначала то, что чаще всего ищут (оригинал всегда первый), каверы в конце;
# отдельно от порядка VERSION_KINDS, который решает, как называть версию
DISPLAY_ORDER = [
    "Lyrics", "Slowed", "Sped up", "Remix", "Live", "Acoustic", "Instrumental",
    "8D", "Bass boosted", "Extended", "Short", "Cover",
]  # fmt: skip

VERSION_QUERIES = ["", "remix", "live", "slowed reverb", "sped up", "acoustic", "instrumental", "cover"]

EXTRA_QUERIES = [
    "slowed",
    "reverb",
    "slowed + reverb full",
    "super slowed",
    "speed up reverb",
    "nightcore",
    "8d audio",
    "bass boosted",
    "extended",
    "full version",
    "radio edit",
    "lyrics",
]

VERSIONS_DEADLINE = 5.0  # секунд на весь поиск версий
FULL_DEADLINE = 9.0  # полный поиск делает больше запросов, ему можно дольше


def version_label(title: str, kinds: list = VERSION_KINDS, song: str = "", artist: str = "") -> str:
    """Тип версии по названию ролика; слова самой песни («Live Forever») и исполнителя («Live») за пометку не считаем."""
    for own in (song, artist):
        if own.strip():
            title = re.sub(re.escape(own.strip()), " ", title, flags=re.I)
    for name, rx in kinds:
        if rx.search(title):
            return name
    return "Original"


async def versions(
    artist: str, title: str, duration: int = 0, limit: int = 12, per_kind: int = 2, full: bool = False
) -> Outcome:
    """Оригинал и его версии: ремиксы, live, slowed, sped up, acoustic, instrumental, каверы.
    Для каждого типа берём до per_kind лучших, чтобы список был разнообразным, а не из одних ремиксов.
    full=True: больше запросов и видов (slowed+reverb, 8D, extended, short…), список длиннее."""
    kinds = VERSION_KINDS + (EXTRA_KINDS if full else [])
    kind_order = sorted((k for k, _ in kinds), key=DISPLAY_ORDER.index)
    queries = VERSION_QUERIES + (EXTRA_QUERIES if full else [])
    base = f"{artist} {title}".strip()
    T = asyncio.create_task
    jobs = [T(safe_call(search_ytm_top, base)), T(safe_call(search_ytm, base, "songs", "ytm"))]
    for suffix in queries:  # версии чаще всего лежат как ролики, поэтому ищем среди «videos»
        jobs.append(T(safe_call(search_ytm, f"{base} {suffix}".strip(), "videos", "ytmv")))
    jobs += [
        T(safe_call(search_audius, base)),
        T(safe_call(search_audius, f"{base} remix")),
        T(safe_call(search_soundcloud, base, 12)),
    ]
    done, pending = await asyncio.wait(
        jobs, timeout=FULL_DEADLINE if full else VERSIONS_DEADLINE
    )  # что не успело, не ждём
    for t in pending:
        t.cancel()
    pool = [r for t in jobs if t in done for r in t.result()]  # порядок: сначала «Top result»

    want = norm(title)
    artist_tokens = [norm(t) for t in re.split(r"[\s,&]+", artist) if len(norm(t)) >= 3]

    def relevant(r: SearchResult) -> bool:
        if not want or want not in norm(r.title):
            return False
        if artist_tokens and not any(t in norm(f"{r.artist} {r.title}") for t in artist_tokens):
            return False  # песня с таким же названием, но другого исполнителя
        return not r.duration or 45 <= r.duration <= 900

    refs = [Canon(artist, title, duration)]
    try:  # точная длительность и написание — из каталога
        dz = await safe_call(deezer, base, 6)
        refs = [
            c
            for c in dz
            if is_clean_canon(c)
            and core_title(c.title)[0] == core_title(title)[0]
            and artist_names(main_artist(artist)) & artist_names(main_artist(c.artist))
        ] or refs
    except Exception:
        pass
    groups: dict[str, list[SearchResult]] = {k: [] for k in ["Original", *kind_order]}
    seen: set[str] = set()
    kept: list[SearchResult] = []
    for r in pool:
        if r.url in seen or not relevant(r) or any(same_track(k, r) for k in kept):
            continue
        seen.add(r.url)
        kept.append(r)
        mark_exact(r, refs)
        r.label = version_label(r.title, kinds, title, artist)
        groups[r.label].append(r)

    originals = groups["Original"]
    exact = [r for r in originals if r.exact]
    # оригиналом считаем только точный трек; точных нет (каталог недоступен) — прежний список
    picked = sorted(exact, key=lambda r: PRIORITY.get(r.source, 4))[:2] or originals[:2]
    for kind in kind_order:
        picked += sorted(groups[kind], key=lambda r: PRIORITY.get(r.source, 4))[:per_kind]
    return Outcome(picked[:limit], "", base, kind="versions")
