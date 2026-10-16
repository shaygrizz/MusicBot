"""Расширенный поиск: много источников параллельно, сверка с каталогами, склейка дублей.

Обычный поиск — быстрый (около 3 с). Глубокий (`deep=True`, кнопка в боте) тратит больше времени:
больше вариантов запроса, больше результатов в каждом источнике, ещё один каталог (Genius).
"""
import asyncio
import json
import logging
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher, get_close_matches

import yt_dlp

import audius
import downloader
import hitmo
from downloader import BAD_VERSION_RE, SearchResult

SOURCE_TIMEOUT = 8  # секунд на каждый источник, медленные отбрасываем
FAST_DEADLINE = float(os.getenv("SEARCH_FAST_DEADLINE", "3.0"))  # быстрые источники (каталоги, YT Music) ждём не дольше
SOFT_DEADLINE = 2.0  # обычный поиск: медленным источникам (SoundCloud, yt-dlp) дольше не ждём
DEEP_DEADLINE = 7.0  # глубокий поиск
MIN_FULL_TRACK = 45  # короче — почти наверняка 30-секундный отрывок
DEDUP_SECONDS = 3
AU_RESERVE = 2  # сколько мест в списке всегда оставляем ремиксам Audius
STRONG_DUR = 3  # разница длительности (с), при которой считаем запись «той самой»
OFFICIAL_DUR = 6  # то же для «официальных» источников (YouTube Music, VK), где длительность надёжнее

# приоритет источника: меньше — выше в списке
PRIORITY = {"verified": 0, "top": 1, "ytm": 1, "sc": 2, "ytmv": 3, "yt": 4, "hm": 4, "au": 4, "bad": 5}
ICONS = {"top": "🎵", "ytm": "🎵", "ytmv": "🎬", "yt": "▶️", "sc": "☁️", "hm": "💿", "au": "🎧"}

STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "on", "my", "me", "you", "your", "it", "is", "im",
             "i", "we", "he", "she", "for", "with", "at", "be", "no", "ft", "feat",
             "и", "в", "на", "я", "ты", "не", "что", "как", "то", "мне", "тебя", "с"}

NOISE_RE = re.compile(
    r"\b(скачать|скачай|download|mp3|песня|песню|трек|музыка|музыку|найди|найти|free|бесплатно)\b",
    re.I,
)


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


def clean_query(q: str) -> str:
    q = NOISE_RE.sub(" ", q)
    q = re.sub(r"[\"“”«»]", " ", q)
    return re.sub(r"\s+", " ", q).strip(" -–—.,!?")


def _fold(s: str) -> str:
    """«São Paulo» -> «Sao Paulo»: убираем диакритику у латиницы (у кириллицы «й», «ё» не трогаем)."""
    out = []
    for ch in s:
        d = unicodedata.normalize("NFD", ch)
        out.append(d[0] if len(d) > 1 and d[0].isascii() else ch)
    return "".join(out)


def _norm(s: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", _fold(s.lower()))
    return re.sub(r"[\W_]+", "", s)


def _get_json(url: str, timeout: int = 10, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MusicBot/1.0", "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------- каталоги (только метаданные, без скачивания) ----------

def _deezer(q: str, n: int = 5) -> list[Canon]:
    data = _get_json(f"https://api.deezer.com/search?limit={n}&q=" + urllib.parse.quote(q))
    return [
        Canon(x["artist"]["name"], x["title"], int(x.get("duration") or 0),
              (x.get("album") or {}).get("cover_big", ""))
        for x in data.get("data", [])
    ]


def _itunes(q: str, n: int = 5) -> list[Canon]:
    data = _get_json(f"https://itunes.apple.com/search?entity=song&limit={n}&term=" + urllib.parse.quote(q))
    return [
        Canon(x["artistName"], x["trackName"], int(x.get("trackTimeMillis") or 0) // 1000,
              (x.get("artworkUrl100") or "").replace("100x100", "400x400"))
        for x in data.get("results", [])
    ]


ARTIST_WORDS_RE = re.compile(
    r"\b(songs?|best|hits?|top|music|band|group|greatest|tracks?|discography|"
    r"песни|песня|лучшее|лучшие|хиты|группа|музыка|треки|дискография)\b", re.I)


FUZZY_ARTIST_FANS = 30_000  # имя с опечаткой принимаем только у исполнителя с таким числом поклонников
FUZZY_ARTIST_RATIO = 0.85  # и похожестью имени не ниже этой
SONG_OVER_ARTIST_FANS = 200_000  # у исполнителя с таким именем меньше поклонников, чем у одноимённой песни
SONG_MIN_RANK = 500_000  # и рейтинг песни в Deezer не ниже этого


def _deezer_artist(q: str, n: int = 12) -> tuple[str, list[Canon]] | None:
    """Если запрос — это имя исполнителя, возвращает его самые популярные песни (и только его).
    «queen» -> Queen: Under Pressure, Bohemian Rhapsody…; «queen under pressure» сюда не попадает."""
    name_q = _norm(ARTIST_WORDS_RE.sub(" ", q))
    if len(name_q) < 2:
        return None
    plain = ARTIST_WORDS_RE.sub(" ", q).strip()  # без слов «songs», «best», «песни» — иначе имя не найдётся
    enc = urllib.parse.quote(plain)
    with ThreadPoolExecutor(2) as ex:  # поиск исполнителя и самого популярного трека — одновременно, чтобы не ждать вдвое дольше
        f_artist = ex.submit(_get_json, "https://api.deezer.com/search/artist?limit=10&q=" + enc)
        f_track = ex.submit(_get_json, "https://api.deezer.com/search?limit=1&q=" + enc)
        data = f_artist.result()
        try:
            tracks = f_track.result().get("data", [])
        except Exception:
            tracks = []
    exact = [a for a in data.get("data", []) if _norm(a["name"]) == name_q]
    if not any((a.get("nb_fan") or 0) >= FUZZY_ARTIST_FANS for a in exact):
        # опечатка в имени («kendric lamar», «eminm»): Deezer её прощает, берём похожее имя, но только у заметных исполнителей —
        # иначе песня с похожим названием сошла бы за исполнителя. Пустые исполнители-двойники («Travis Scot») не мешают
        exact += [a for a in data.get("data", []) if a not in exact and (a.get("nb_fan") or 0) >= FUZZY_ARTIST_FANS
                  and SequenceMatcher(None, _norm(a["name"]), name_q).ratio() >= FUZZY_ARTIST_RATIO]
    if not exact:
        return None
    artist = max(exact, key=lambda a: a.get("nb_fan") or 0)  # исполнителей с одним именем много — берём самого известного
    if (artist.get("nb_fan") or 0) < SONG_OVER_ARTIST_FANS:
        # «believer», «blinding lights»: у малоизвестной группы то же имя, что у знаменитой песни — ищут песню
        if tracks and _norm(tracks[0]["title"]) == name_q and (tracks[0].get("rank") or 0) >= SONG_MIN_RANK:
            return None
    top = _get_json(f"https://api.deezer.com/artist/{artist['id']}/top?limit={n * 3}").get("data", [])
    me = _norm(artist["name"])
    out: list[Canon] = []
    for t in top:
        credited = {_norm(t["artist"]["name"])} | {_norm(c.get("name", "")) for c in t.get("contributors", [])}
        if me not in credited:
            continue  # чужая песня, где исполнитель лишь упомянут
        title = t["title"]
        if BAD_VERSION_RE.search(title) and not BAD_VERSION_RE.search(q):
            continue  # live, remix и т.п. — не «главные» версии
        if any(_norm(title) == _norm(c.title) for c in out):
            continue
        out.append(Canon(artist["name"], title, int(t.get("duration") or 0),
                         (t.get("album") or {}).get("cover_big", "")))
        if len(out) >= n:
            break
    return (artist["name"], out) if out else None


def _lazy_url(c: Canon) -> str:
    """Песня из каталога без ссылки на скачивание: источник подберётся в момент нажатия."""
    return "lazy://" + urllib.parse.quote(f"{c.artist}|{c.title}|{c.duration}", safe="")


def lazy_for(artist: str, title: str, duration: int = 0) -> str:
    """Ссылка-заглушка для песни, у которой нет адреса скачивания (избранное): источник подберётся при воспроизведении."""
    return _lazy_url(Canon(artist, title, duration))


def _genius(q: str) -> list[Canon]:
    """Публичный поиск Genius: хорошо нормализует «исполнитель — название» (длительности нет)."""
    data = _get_json("https://genius.com/api/search/song?per_page=5&q=" + urllib.parse.quote(q))
    hits = (data.get("response", {}).get("sections") or [{}])[0].get("hits", [])
    out = []
    for h in hits:
        r = h.get("result", {})
        artist = (r.get("primary_artist") or {}).get("name", "")
        title = r.get("title", "")
        if artist and title and "Genius" not in artist:  # пропускаем страницы переводов
            out.append(Canon(artist, title, 0, r.get("song_art_image_thumbnail_url", "")))
    return out


LYRIC_MIN_WORDS = 3  # от скольких слов запрос считаем «строчкой из песни»
KNOWN_RANK = 100_000  # рейтинг Deezer, с которого песню считаем известной (у малоизвестных — ноль или единицы тысяч)
LYRIC_CANDIDATES = 8  # сколько песен с подходящей строкой рассматриваем; берём самую известную


def _clean_genius_title(title: str) -> str:
    """«Мама, я танцую (Mom, I Dance)» -> «Мама, я танцую»: английский перевод в скобках не нужен."""
    main, _, tail = title.partition(" (")
    if re.search("[а-яё]", main, re.I) and tail and not re.search("[а-яё]", tail, re.I):
        return main.strip()
    return title.strip()


def _sings_it(hit: dict, q: str, fuzzy: bool = False) -> bool:
    """В тексте песни есть строка, почти совпадающая с запросом. Если запрос лишь часть длинной строки
    («Ik voel me tame impala the less I know the better»), песня его только упоминает, а не поёт."""
    want = _norm(q)
    if len(want) < 8:
        return False
    for hl in hit.get("highlights") or []:
        for line in str(hl.get("value", "")).splitlines():
            ln = _norm(line)
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
    data = _get_json("https://genius.com/api/search/lyric?per_page=10&q=" + urllib.parse.quote(q))
    out: list[Canon] = []
    for sec in data.get("response", {}).get("sections", []):
        for h in sec.get("hits", []):
            r = h.get("result", {})
            artist = (r.get("primary_artist") or {}).get("name", "")
            title = _clean_genius_title(r.get("title", ""))
            if not artist or not title or "Genius" in artist:  # страницы переводов
                continue
            if not _sings_it(h, q, fuzzy):
                continue
            if BAD_VERSION_RE.search(title) and not BAD_VERSION_RE.search(q):
                continue
            if any(_norm(title) == _norm(c.title) and _norm(artist) == _norm(c.artist) for c in out):
                continue  # ремастеры и повторы одной песни
            out.append(Canon(artist, title, 0, r.get("song_art_image_thumbnail_url", "")))
            if len(out) >= LYRIC_CANDIDATES:
                return out
    return out


def _enrich(c: Canon) -> Canon:
    """Длительность, обложка и известность из Deezer: по длительности проверяется, что скачалась именно эта песня."""
    try:
        want_t, want_a = _norm(c.title), _norm(c.artist)
        data = _get_json(f"https://api.deezer.com/search?limit=5&q=" + urllib.parse.quote(f"{c.artist} {c.title}"))
        for x in data.get("data", []):
            name = x["artist"]["name"]
            if _norm(x["title"]) == want_t and (want_a in _norm(name) or _norm(name) in want_a):
                return Canon(name, x["title"], int(x.get("duration") or 0),
                             (x.get("album") or {}).get("cover_big", "") or c.thumb, int(x.get("rank") or 0))
    except Exception:
        pass
    return c


def _respell(q: str, titles: list[str]) -> str:
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


def _lyric_candidates(q: str, fuzzy: bool = False) -> list[Canon]:
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


# ---------- источники со скачиваемыми результатами ----------

def _dur(e: dict) -> int:
    if e.get("duration_seconds"):
        return int(e["duration_seconds"])
    parts = str(e.get("duration") or "").split(":")
    if parts and all(p.isdigit() for p in parts):
        sec = 0
        for p in parts:
            sec = sec * 60 + int(p)
        return sec
    return 0


def _to_result(e: dict, tag: str) -> SearchResult | None:
    vid = e.get("videoId")
    if not vid:
        return None
    artists = e.get("artists") or []
    thumbs = e.get("thumbnails") or []
    return SearchResult(
        id=vid, title=e.get("title") or "Без названия",
        artist=", ".join(a["name"] for a in artists if a.get("name")),
        duration=_dur(e), url=f"https://www.youtube.com/watch?v={vid}",
        thumb_url=downloader._small_thumb(thumbs[-1]["url"]) if thumbs else "", source=tag,
    )


def _ytm(q: str, filt: str, tag: str) -> list[SearchResult]:
    items = downloader._get_ytm().search(q, filter=filt, limit=8)
    return [r for r in (_to_result(e, tag) for e in items) if r]


def _ytm_top(q: str) -> list[SearchResult]:
    """Поиск без фильтра: «Top result» — то, что YouTube Music считает самым точным совпадением."""
    items = downloader._get_ytm().search(q)
    out = []
    for e in items:
        kind = e.get("resultType")
        if kind not in ("song", "video"):
            continue
        tag = "top" if e.get("category") == "Top result" else ("ytm" if kind == "song" else "ytmv")
        r = _to_result(e, tag)
        if r:
            out.append(r)
    return out[:6]


def _au(q: str) -> list[SearchResult]:
    return [
        SearchResult(id=t["id"], title=t["title"], artist=t["artist"], duration=t["duration"],
                     url=f"audius://{t['id']}", thumb_url=t["cover"], source="au")
        for t in audius.search(q, 8)
        if not t["duration"] or t["duration"] >= MIN_FULL_TRACK
    ]


def _hm(q: str) -> list[SearchResult]:
    """Российский агрегатор mp3: готовые файлы с длительностью, поэтому хорошо сверяются со справочником."""
    return [
        SearchResult(id=t["id"], title=t["title"], artist=t["artist"], duration=t["duration"],
                     url="hitmo://" + urllib.parse.quote(t["url"], safe=""), thumb_url=t["cover"], source="hm")
        for t in hitmo.search(q, 8)
        if not t["duration"] or t["duration"] >= MIN_FULL_TRACK
    ]


def _yandex(q: str, n: int = 5) -> list[Canon]:
    """Яндекс Музыка как русский справочник (только метаданные и обложки): поиск открытый, файлы по подписке."""
    data = _get_json("https://api.music.yandex.net/search?type=track&page=0&text=" + urllib.parse.quote(q),
                     headers={"X-Yandex-Music-Client": "YandexMusicAndroid/24023621"})
    items = ((data.get("result") or {}).get("tracks") or {}).get("results", [])
    out = []
    for t in items[:n]:
        artists = ", ".join(a["name"] for a in t.get("artists", []) if a.get("name"))
        cover = t.get("coverUri") or ""
        out.append(Canon(artists, t.get("title", ""), int(t.get("durationMs") or 0) // 1000,
                         "https://" + cover.replace("%%", "400x400") if cover else ""))
    return [c for c in out if c.artist and c.title]


def _yt(q: str, n: int = 5) -> list[SearchResult]:
    res = downloader._search_ytdlp_sync(q, n)
    for r in res:
        r.source = "yt"
    return res


def _sc(q: str, n: int = 8) -> list[SearchResult]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
            "socket_timeout": 8, "retries": 1, "extractor_retries": 1}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch{n}:{q}", download=False)
    out = []
    for e in info.get("entries", []) or []:
        url, dur = e.get("url"), int(e.get("duration") or 0)
        if not url or (dur and dur < MIN_FULL_TRACK):
            continue
        out.append(SearchResult(
            id=str(e.get("id") or url), title=e.get("title") or "Без названия",
            artist=e.get("uploader") or "", duration=dur, url=url, source="sc",
        ))
    return out


def _done(t: "asyncio.Task"):
    """Результат задачи, если она успела; иначе пусто (источник опоздал или упал)."""
    if t.done() and not t.cancelled() and t.exception() is None:
        return t.result()
    return []


async def _safe(fn, *args):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), SOURCE_TIMEOUT)
    except Exception as e:
        logging.info("источник %s не ответил: %s", getattr(fn, "__name__", fn), str(e)[:80])
        return []


# ---------- сверка и сборка ----------

def _tokens(query: str) -> list[str]:
    return [t for t in (_norm(w) for w in query.split()) if len(t) >= 2]


def _canon_relevant(c: Canon, tokens: list[str]) -> bool:
    """Запись каталога годится, если слова запроса, не вошедшие в имя исполнителя, есть в названии.
    Так «tame impala loser» не тянет за собой другие песни Tame Impala."""
    if not tokens:
        return True
    artist_n, title_n = _norm(c.artist), _norm(c.title)
    rest = [t for t in tokens if t not in artist_n]
    if not rest:
        return True  # в запросе только исполнитель
    meaningful = [t for t in rest if t not in STOPWORDS] or rest  # «the», «of», «you» не считаем
    words = [_norm(w) for w in re.findall(r"\w+", c.title)]

    def in_title(t: str) -> bool:  # «paolo» ~ «paulo»: опечатка в одну-две буквы не мешает
        return t in title_n or (len(t) >= 4 and any(SequenceMatcher(None, t, w).ratio() >= 0.8 for w in words if w))
    hit = sum(1 for t in meaningful if in_title(t))
    return hit / len(meaningful) >= 0.7


def _artist_ok(r: SearchResult, c: Canon) -> bool:
    text = _norm(f"{r.artist} {r.title}")
    toks = [t for t in re.split(r"[\s,&]+", c.artist.lower()) if len(_norm(t)) >= 3]
    return not toks or any(_norm(t) in text for t in toks)


def _verify(r: SearchResult, canons: list[Canon]) -> Canon | None:
    nt = _norm(r.title)
    official = r.source in ("ytm", "top")
    for c in canons:
        ct = _norm(c.title)
        if not ct or ct not in nt or not _artist_ok(r, c):
            continue
        if r.duration and c.duration:
            if abs(r.duration - c.duration) > (OFFICIAL_DUR if official else STRONG_DUR):
                continue
        elif not official:
            continue  # без длительности подтверждаем только официальные источники
        return c
    return None


# ---------- «точный трек»: оригинал без изменений ----------

_PAREN_RE = re.compile(r"[(\[]([^)\]]*)[)\]]")
# пометки в скобках, которые трек не меняют (feat., official audio, remastered, from "…", год)
_SAFE_PAREN_RE = re.compile(
    r"^\s*(feat\b|ft\b|featuring|with\b|prod\b|official|audio|video|lyric|remaster|from\b|visuali[sz]er|"
    r"hd\b|hq\b|4k|explicit|clean|music|topic|\d{4}\b)", re.I)
_NOISE_TAIL_RE = re.compile(r"\s[-–|]\s*(official|audio|video|lyrics?|music video|hd|hq|4k|visuali[sz]er|topic)\b.*$", re.I)
_FEAT_SPLIT_RE = re.compile(r"\s(?:feat\.?|ft\.?|featuring|with)\s", re.I)
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b)\s*", re.I)
EXACT_DURATION = 8  # секунд: во столько длительность точного трека может отличаться от каталожной


def _main_artist(artist: str) -> str:
    return _ARTIST_SPLIT_RE.split(artist.strip())[0].strip()


def _core(title: str, artist: str = "") -> tuple[str, list[str]]:
    """Название без «Artist - », feat. и пометок + список пометок из скобок."""
    t = _NOISE_TAIL_RE.sub("", title)
    if artist:
        t = re.sub(rf"^\s*{re.escape(artist)}\b[^-–:]*?\s*[-–:]\s*", "", t, flags=re.I)
    quals = _PAREN_RE.findall(t)
    t = _FEAT_SPLIT_RE.split(_PAREN_RE.sub(" ", t))[0]
    return _norm(t), quals


def _artist_names(artist: str) -> set[str]:
    out = set()
    for part in _ARTIST_SPLIT_RE.split(artist):
        n = _norm(re.sub(r"\b(topic|vevo|official)\b", " ", part, flags=re.I))
        if n:
            out.add(n)
            out.add(n.removeprefix("the"))
    return out


def _clean_canon(c: Canon) -> bool:
    """Запись каталога без пометок (Single Version, Live, Acapella…): только такие считаем эталоном."""
    return not BAD_VERSION_RE.search(c.title) and all(_SAFE_PAREN_RE.match(q) for q in _core(c.title)[1])


def _is_exact(r: SearchResult, c: Canon) -> bool:
    """Это тот самый трек из каталога: название и исполнитель те же, без ремикса/live/кавера/версий,
    длительность как у оригинала (не отрывок и не удлинённая версия)."""
    main = _main_artist(c.artist)
    ct, _ = _core(c.title)
    rt, quals = _core(r.title, main)
    if not ct or rt != ct:
        return False
    if not all(_SAFE_PAREN_RE.match(q) for q in quals):
        return False
    if BAD_VERSION_RE.search(r.title):
        return False
    want = _artist_names(main)
    have = _artist_names(r.artist) if r.artist else set()
    prefixed = bool(re.match(rf"^\s*{re.escape(main)}\b", r.title, re.I))  # «Artist - Title» в названии ролика
    if not (want & have or prefixed):
        return False  # «The Retro Weeknd» — не The Weeknd
    if c.duration and r.duration:
        return abs(r.duration - c.duration) <= EXACT_DURATION
    if c.duration and not r.duration:
        return r.source in ("ytm", "top")  # у официальных «песен» YouTube Music длительность иногда не отдаётся
    return True


def _mark_exact(r: SearchResult, refs: list[Canon]) -> None:
    for c in refs:
        if _is_exact(r, c):
            r.exact = True
            if not r.duration:
                r.duration = c.duration  # длительность неизвестна — берём каталожную, чтобы скачивание могло отбраковать обрезанный файл
            return
    r.exact = False


def _exact_refs(canons: list[Canon], query: str) -> list[Canon]:
    """Эталон для проверки «точности»: самая популярная чистая запись каталога (каталог отдаёт её первой)
    и её дубли с той же длительностью. Другие песни с таким названием и версии другой длины эталоном не служат."""
    clean = [c for c in canons if _clean_canon(c)]
    if not clean:
        return []
    q = _norm(query)
    # исполнитель назван, если его имя есть в запросе вместе с другими словами (запрос «starboy» — это название песни,
    # а не исполнитель Starboy)
    named = [c for c in clean if any(n and n in q and len(q) > len(n) + 1 for n in _artist_names(_main_artist(c.artist)))]
    pool = named or clean
    top = pool[0]
    who = _artist_names(_main_artist(top.artist))
    return [c for c in pool
            if _core(c.title)[0] == _core(top.title)[0] and _artist_names(_main_artist(c.artist)) & who
            and (not top.duration or not c.duration or abs(c.duration - top.duration) <= EXACT_DURATION)]


def _rank(r: SearchResult, query: str) -> int:
    if r.exact:  # точные треки всегда впереди: внутри — по источнику (официальные «песни» YouTube Music первыми)
        return PRIORITY[r.source or "yt"]
    if BAD_VERSION_RE.search(r.title) and not BAD_VERSION_RE.search(query):
        return PRIORITY["bad"] + 10  # каверы, ремиксы, slowed — только если их просили
    if r.verified:
        return PRIORITY["verified"] + 10
    return PRIORITY[r.source or "yt"] + 10


def _relevant(r: SearchResult, tokens: list[str], strict: bool,
              must: list[str] | None = None, ref_dur: int = 0) -> bool:
    """Результат без совпадения с каталогом должен пересекаться с запросом по словам.
    Обычный поиск требует большую часть слов (чужие «LOSER» не нужны), глубокий — хотя бы одно."""
    if r.verified or not tokens:
        return True
    text = _norm(f"{r.artist} {r.title}")
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


def _same_track(a: SearchResult, b: SearchResult) -> bool:
    if _norm(a.title) != _norm(b.title):
        return False
    if a.duration and b.duration:
        return abs(a.duration - b.duration) <= DEDUP_SECONDS
    return _norm(a.artist) == _norm(b.artist)  # длительность неизвестна — сравниваем по исполнителю


def _merge(pool: list[SearchResult], canons: list[Canon], query: str, limit: int,
           strict: bool = True) -> list[SearchResult]:
    for r in pool:
        c = _verify(r, canons)
        if c:  # нашли совпадение в каталоге — подставляем чистые название, исполнителя, обложку
            r.verified = True
            r.title, r.artist = c.title, c.artist
            if c.thumb and not r.thumb_url:
                r.thumb_url = c.thumb
    refs = _exact_refs(canons, query)
    for r in pool:
        _mark_exact(r, refs)
    tokens = _tokens(query)
    must, ref_dur = [], 0
    if canons and strict:  # по лучшей записи каталога понимаем, какие слова — название, и сколько оно длится
        must = [t for t in tokens if t not in _norm(canons[0].artist)]
        ref_dur = canons[0].duration
    seen_urls, kept = set(), []
    for r in sorted(pool, key=lambda r: _rank(r, query)):  # sorted стабильна: порядок источника сохраняется
        if r.url in seen_urls or not _relevant(r, tokens, strict, must, ref_dur):
            continue
        if any(_same_track(k, r) for k in kept):
            continue
        seen_urls.add(r.url)
        kept.append(r)
    # ремиксы Audius в общем рейтинге всегда внизу и вытеснились бы — оставляем им до двух мест в конце
    au = [r for r in kept if r.source == "au"][:AU_RESERVE]
    others = [r for r in kept if r.source != "au"]
    return others[: max(0, limit - len(au))] + au


def _dedupe_canons(parts: list[list[Canon]]) -> list[Canon]:
    out: list[Canon] = []
    for part in parts:
        for c in part:
            if not any(_norm(c.title) == _norm(k.title) and _norm(c.artist) == _norm(k.artist) for k in out):
                out.append(c)
    return out


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
    tokens = _tokens(q)
    n_cat = 15 if deep else 5  # сколько записей просим у каталогов
    loop = asyncio.get_running_loop()
    start = loop.time()
    T = asyncio.create_task

    # шаг 1: все источники стартуют сразу. Быстрые ждём, медленные — только до мягкого дедлайна
    fast = {
        "top": T(_safe(_ytm_top, q)),
        "ytm": T(_safe(_ytm, q, "songs", "ytm")),
        "dz": T(_safe(_deezer, q, n_cat)),
        "it": T(_safe(_itunes, q, n_cat)),
        "ym": T(_safe(_yandex, q, n_cat)),
        "art": T(_safe(_deezer_artist, q, 25 if deep else 12)),
    }
    slow = {
        "ytmv": T(_safe(_ytm, q, "videos", "ytmv")),
        "yt": T(_safe(_yt, q, 10 if deep else 5)),
        "sc": T(_safe(_sc, q, 15 if deep else 8)),
        "au": T(_safe(_au, q)),
        "hm": T(_safe(_hm, q)),
    }
    if len(words) >= 4:  # похоже на строчку из песни — ищем ещё и «текстовые» ролики
        slow["lyr"] = T(_safe(_ytm, q + " lyrics", "videos", "ytmv"))
    if deep:
        slow["gen"] = T(_safe(_genius, q))
        slow["oa"] = T(_safe(_ytm, q + " official audio", "videos", "ytmv"))
        slow["lyr2"] = T(_safe(_ytm, q + " lyrics", "songs", "ytm"))
    lyric = T(_safe(_lyric_candidates, q)) if len(words) >= LYRIC_MIN_WORDS and not fast_only else None
    # Deezer и проверка «это имя исполнителя?» отвечают первыми — по ним решаем, что делать дальше
    await asyncio.wait([fast["dz"], fast["art"]], timeout=FAST_DEADLINE)

    artist_hit = _done(fast["art"]) or None
    if artist_hit:  # «queen» -> только песни Queen, остальные источники не нужны
        for t in list(fast.values()) + list(slow.values()) + ([lyric] if lyric else []):
            if not t.done():
                t.cancel()
        name, tracks = artist_hit
        results = [
            SearchResult(id=f"lz{i}", title=c.title, artist=c.artist, duration=c.duration,
                         url=_lazy_url(c), thumb_url=c.thumb, source="top", verified=True)
            for i, c in enumerate(tracks)
        ]
        return Outcome(results, "", q, kind="artist", artist=name)

    canons_dz = [c for c in _done(fast["dz"]) if _canon_relevant(c, tokens)]

    # шаг 2 стартует сразу, не дожидаясь остальных источников: по точным названиям из каталога
    # ищем скачиваемые версии (то, что не нашлось по сырому запросу)
    second_songs = [T(_safe(_ytm, f"{c.artist} {c.title}", "songs", "ytm")) for c in canons_dz[:3]]
    second_top = [T(_safe(_ytm_top, f"{c.artist} {c.title}")) for c in canons_dz[:2]]
    group = [t for k, t in fast.items() if k not in ("dz", "art")] + second_songs + second_top
    # один зависший источник (например, Яндекс с другого адреса) не должен тормозить весь поиск
    await asyncio.wait(group, timeout=max(0.3, FAST_DEADLINE - (loop.time() - start)))
    late = [k for k, t in fast.items() if not t.done()]
    for t in group:
        if not t.done():
            t.cancel()

    canons = _dedupe_canons([canons_dz, [c for c in _done(fast["it"]) + _done(fast["ym"])
                                         if _canon_relevant(c, tokens)]])

    if fast_only:  # подбор версии при нажатии на трек: медленные источники не ждём совсем
        for t in slow.values():
            t.cancel()
        slow = {}
    left = max(0.0, (DEEP_DEADLINE if deep else SOFT_DEADLINE) - (loop.time() - start))
    if left and slow:
        await asyncio.wait(slow.values(), timeout=left)

    # порядок в пуле = приоритет при равных: «Top result», найденное по каталогу, затем всё остальное
    pool: list[SearchResult] = list(_done(fast["top"]))
    for t in second_top + second_songs:
        pool.extend(_done(t))
    pool.extend(_done(fast["ytm"]))
    extra_canons: list[Canon] = []
    for key, t in slow.items():
        if not t.done():
            t.cancel()  # поток дождётся своего таймаута, результат нам уже не нужен
            continue
        if key == "gen":
            extra_canons = [c for c in _done(t) if _canon_relevant(c, tokens)]
        else:
            pool.extend(_done(t))
    canons = _dedupe_canons([canons, extra_canons])
    late += [k for k, t in slow.items() if not t.done() or t.cancelled()]
    logging.info("поиск %r: %.1f с, не успели: %s", q, loop.time() - start, ", ".join(late) or "никто")

    if deep:
        results = _merge(pool, canons, q, 14, strict=False)
    else:
        results = _merge(pool, canons, q, limit)
        if len(results) < 3:  # строгий фильтр оставил мало — ослабляем (например, строчка из песни)
            results = _merge(pool, canons, q, limit, strict=False)
    # «исполнитель + название» в запросе — это не строчка из песни, поиск по тексту только внесёт шум
    named = any(_norm(c.artist) in _norm(q) and _norm(c.title) in _norm(q) for c in canons_dz)
    short_title = bool(canons_dz) and len(words) < 5  # короткий запрос, по названию нашлось: это название, а не текст
    if lyric and not named and not short_title:  # запрос мог быть строчкой из песни, а не названием: Genius ищет по словам из текста
        await asyncio.wait([lyric], timeout=max(1.5, FAST_DEADLINE - (loop.time() - start)))
        by_text = _done(lyric)
        if not by_text:  # ничего не нашлось — возможно, в запросе опечатки: правим их по найденным названиям
            fixed = _respell(q, [f"{r.artist} {r.title}" for r in pool] + [f"{c.artist} {c.title}" for c in canons])
            if fixed.lower() != q.lower():
                try:
                    by_text = await asyncio.wait_for(asyncio.to_thread(_lyric_candidates, fixed, True), 4.0)
                    logging.info("поиск по тексту: опечатки %r -> %r", q, fixed)
                except Exception as e:
                    logging.info("поиск по тексту с исправлением не удался: %s", str(e)[:80])
        if by_text:
            top = [SearchResult(id=f"ly{i}", title=c.title, artist=c.artist, duration=c.duration,
                                url=_lazy_url(c), thumb_url=c.thumb, source="top", verified=True)
                   for i, c in enumerate(by_text)]
            rest = [r for r in results if not any(_norm(r.title) == _norm(c.title) and _artist_ok(r, c)
                                                  for c in by_text)]
            # по названию ничего не нашлось и песня известная -> первая; иначе (чужие загрузки, малоизвестная) -> второй
            at = 0 if not canons_dz and by_text[0].rank >= KNOWN_RANK else min(1, len(rest))  # неизвестную — второй
            results = (rest[:at] + top + rest[at:])[: 14 if deep else limit]
            logging.info("поиск по тексту %r: %s", q, "; ".join(f"{c.artist} - {c.title}" for c in by_text))
    if lyric and not lyric.done():
        lyric.cancel()
    hint = ""
    if not results and canons:
        # скачиваемого не нашлось (источники медленные или закрыты), но песня есть в каталоге: источник подберётся при нажатии
        clean = [c for c in canons if _clean_canon(c)] or canons
        results = [SearchResult(id=f"cn{i}", title=c.title, artist=c.artist, duration=c.duration,
                                url=_lazy_url(c), thumb_url=c.thumb, source="top", verified=True)
                   for i, c in enumerate(clean[:3])]
        hint = f"{canons[0].artist} — {canons[0].title}"
    return Outcome(results, hint, q)


# ---------- разные версии одной песни (оригинал, ремиксы, live, slowed…) ----------

VERSION_KINDS = [
    ("Remix", re.compile(r"\b(remix|rmx|bootleg|rework|flip|edit|mashup)\b", re.I)),
    ("Live", re.compile(r"\b(live|concert|session|unplugged|tour)\b", re.I)),
    ("Slowed", re.compile(r"\b(slowed|reverb)\b", re.I)),
    ("Sped up", re.compile(r"\b(sped\s*up|speed\s*up|nightcore)\b", re.I)),
    ("Acoustic", re.compile(r"\b(acoustic|piano version|stripped)\b", re.I)),
    ("Instrumental", re.compile(r"\b(instrumental|karaoke|backing track)\b", re.I)),
    ("Cover", re.compile(r"\b(covers?|tribute|кавер)\b", re.I)),
]
KIND_ORDER = [k for k, _ in VERSION_KINDS]
VERSION_QUERIES = ["", "remix", "live", "slowed reverb", "sped up", "acoustic", "instrumental", "cover"]
VERSIONS_DEADLINE = 5.0  # секунд на весь поиск версий


def version_label(title: str) -> str:
    for name, rx in VERSION_KINDS:
        if rx.search(title):
            return name
    return "Original"


async def versions(artist: str, title: str, duration: int = 0, limit: int = 12, per_kind: int = 2) -> Outcome:
    """Оригинал и его версии: ремиксы, live, slowed, sped up, acoustic, instrumental, каверы.
    Для каждого типа берём до per_kind лучших, чтобы список был разнообразным, а не из одних ремиксов."""
    base = f"{artist} {title}".strip()
    T = asyncio.create_task
    jobs = [T(_safe(_ytm_top, base)), T(_safe(_ytm, base, "songs", "ytm"))]
    for suffix in VERSION_QUERIES:  # версии чаще всего лежат как ролики, поэтому ищем среди «videos»
        jobs.append(T(_safe(_ytm, f"{base} {suffix}".strip(), "videos", "ytmv")))
    jobs += [T(_safe(_au, base)), T(_safe(_au, f"{base} remix")), T(_safe(_sc, base, 12))]
    done, pending = await asyncio.wait(jobs, timeout=VERSIONS_DEADLINE)  # что не успело, не ждём
    for t in pending:
        t.cancel()
    pool = [r for t in jobs if t in done for r in t.result()]  # порядок: сначала «Top result»

    want = _norm(title)
    artist_tokens = [_norm(t) for t in re.split(r"[\s,&]+", artist) if len(_norm(t)) >= 3]

    def relevant(r: SearchResult) -> bool:
        if not want or want not in _norm(r.title):
            return False
        if artist_tokens and not any(t in _norm(f"{r.artist} {r.title}") for t in artist_tokens):
            return False  # песня с таким же названием, но другого исполнителя
        return not r.duration or 45 <= r.duration <= 900

    refs = [Canon(artist, title, duration)]
    try:  # точная длительность и написание — из каталога
        dz = await _safe(_deezer, base, 6)
        refs = [c for c in dz if _clean_canon(c) and _core(c.title)[0] == _core(title)[0]
                and _artist_names(_main_artist(artist)) & _artist_names(_main_artist(c.artist))] or refs
    except Exception:
        pass
    groups: dict[str, list[SearchResult]] = {k: [] for k in ["Original", *KIND_ORDER]}
    seen: set[str] = set()
    kept: list[SearchResult] = []
    for r in pool:
        if r.url in seen or not relevant(r) or any(_same_track(k, r) for k in kept):
            continue
        seen.add(r.url)
        kept.append(r)
        _mark_exact(r, refs)
        r.label = version_label(r.title)
        groups[r.label].append(r)

    originals = groups["Original"]
    exact = [r for r in originals if r.exact]
    # оригиналом считаем только точный трек; точных нет (каталог недоступен) — прежний список
    picked = sorted(exact, key=lambda r: PRIORITY.get(r.source, 4))[:2] or originals[:2]
    for kind in KIND_ORDER:
        picked += sorted(groups[kind], key=lambda r: PRIORITY.get(r.source, 4))[:per_kind]
    return Outcome(picked[:limit], "", base, kind="versions")


# ---------- подбор версий и скачивание с автоповтором ----------

def _is_lazy(url: str) -> bool:
    return url.startswith("lazy://")


async def resolve(meta: SearchResult, exclude: set[str] | None = None, limit: int = 4) -> list[SearchResult]:
    """Скачиваемые версии нужного трека (точное название, близкая длительность), лучшие первыми."""
    exclude = exclude or set()
    query = f"{meta.artist} {meta.title}".strip()
    out = await search(query, 10, fast_only=True)  # быстро: YouTube Music + каталог
    if not [r for r in out.results if r.url not in exclude and not _is_lazy(r.url)]:
        out = await search(query, 10)  # быстрых источников не хватило — полный поиск
    want = _norm(meta.title)
    asked_bad = bool(BAD_VERSION_RE.search(meta.title))
    artist_tokens = [_norm(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_norm(t)) >= 3]

    def usable(r: SearchResult) -> bool:
        return r.url not in exclude and not _is_lazy(r.url)

    def exact(r: SearchResult) -> bool:
        if want and want not in _norm(r.title):
            return False
        # то же название у другого исполнителя («The Way I Still Love You» вместо «still love you») — чужая песня
        if artist_tokens and not any(t in _norm(f"{r.artist} {r.title}") for t in artist_tokens):
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
    real = await asyncio.to_thread(downloader.probe_duration, d.path)
    return bool(real) and abs(real - meta.duration) > DURATION_GUARD


async def fetch_audio(url: str, meta: SearchResult | None) -> "downloader.Downloaded":
    """Скачивает трек. Не получилось по выбранной ссылке — пробует другие версии того же трека,
    и только потом запасные источники (SoundCloud, dydki). Ссылка без метаданных качается как есть."""
    if meta is None or not meta.title:
        return await downloader.download(url, "audio", meta)

    tried: set[str] = set()
    last: Exception | None = None
    candidates: list[SearchResult] = []
    if not _is_lazy(url):
        candidates.append(SearchResult(id="", title=meta.title, artist=meta.artist, duration=meta.duration,
                                       url=url, thumb_url=meta.thumb_url, source=meta.source))
    first_round = True
    while True:
        for cand in candidates:
            tried.add(cand.url)
            try:
                d = await downloader.download(cand.url, "audio", cand if cand.url != url else meta, fallbacks=False)
            except Exception as e:
                if downloader.TOO_BIG in str(e):
                    raise
                last = e
                logging.info("версия не скачалась (%s): %s", cand.url, str(e)[:120])
                continue
            if await _wrong_length(d, meta):  # скачалась другая песня: отбрасываем, а не подписываем чужое правильным названием
                logging.info("версия отброшена: длина файла не совпадает с %s с (%s)", meta.duration, cand.url)
                last = RuntimeError("длительность файла не совпадает с песней")
                d.cleanup()
                continue
            d.title, d.artist = meta.title, meta.artist or d.artist  # название и исполнитель из каталога
            return d
        if not first_round:
            break
        first_round = False
        candidates = (await resolve(meta, tried, 3))
        if not candidates:
            break
    try:  # запасные источники по названию
        d = await downloader.download("title://", "audio", meta)
        if await _wrong_length(d, meta):
            d.cleanup()
            raise RuntimeError("длительность файла не совпадает с песней")
        d.title, d.artist = meta.title, meta.artist or d.artist
        return d
    except Exception as e:
        raise last or e
