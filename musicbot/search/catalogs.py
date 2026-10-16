"""Каталоги (Deezer, iTunes, Яндекс Музыка, Genius): только метаданные, без скачивания."""

import logging
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

from ..textutil import BAD_VERSION_RE, norm
from .http import get_json
from .models import Canon
from .text import STOPWORDS


def deezer(q: str, n: int = 5) -> list[Canon]:
    data = get_json(f"https://api.deezer.com/search?limit={n}&q=" + urllib.parse.quote(q))
    return [
        Canon(x["artist"]["name"], x["title"], int(x.get("duration") or 0), (x.get("album") or {}).get("cover_big", ""))
        for x in data.get("data", [])
    ]


def itunes(q: str, n: int = 5) -> list[Canon]:
    data = get_json(f"https://itunes.apple.com/search?entity=song&limit={n}&term=" + urllib.parse.quote(q))
    return [
        Canon(
            x["artistName"],
            x["trackName"],
            int(x.get("trackTimeMillis") or 0) // 1000,
            (x.get("artworkUrl100") or "").replace("100x100", "400x400"),
        )
        for x in data.get("results", [])
    ]


ARTIST_WORDS_RE = re.compile(
    r"\b(songs?|best|hits?|top|music|band|group|greatest|tracks?|discography|"
    r"песни|песня|лучшее|лучшие|хиты|группа|музыка|треки|дискография)\b",
    re.I,
)


FUZZY_ARTIST_FANS = 30_000  # имя с опечаткой принимаем только у исполнителя с таким числом поклонников

FUZZY_ARTIST_RATIO = 0.85  # и похожестью имени не ниже этой

SONG_OVER_ARTIST_FANS = 200_000  # у исполнителя с таким именем меньше поклонников, чем у одноимённой песни

SONG_MIN_RANK = 500_000  # и рейтинг песни в Deezer не ниже этого


def _artist_info(artist_id: int) -> dict | None:
    try:
        found = get_json(f"https://api.deezer.com/artist/{artist_id}")
    except Exception:
        return None
    return found if found.get("id") and found.get("name") else None


def deezer_artist(q: str, n: int = 12) -> tuple[str, list[Canon]] | None:
    """Если запрос — это имя исполнителя, возвращает его самые популярные песни (и только его).
    «queen» -> Queen: Under Pressure, Bohemian Rhapsody…; «queen under pressure» сюда не попадает."""
    name_q = norm(ARTIST_WORDS_RE.sub(" ", q))
    if len(name_q) < 2:
        return None
    plain = ARTIST_WORDS_RE.sub(" ", q).strip()  # без слов «songs», «best», «песни» — иначе имя не найдётся
    enc = urllib.parse.quote(plain)
    # поиск исполнителя и самого популярного трека — одновременно, чтобы не ждать вдвое дольше
    with ThreadPoolExecutor(2) as ex:
        f_artist = ex.submit(get_json, "https://api.deezer.com/search/artist?limit=10&q=" + enc)
        f_track = ex.submit(get_json, "https://api.deezer.com/search?limit=10&q=" + enc)
        data = f_artist.result()
        try:
            tracks = f_track.result().get("data", [])
        except Exception:
            tracks = []
    exact = [a for a in data.get("data", []) if norm(a["name"]) == name_q]
    # поиск исполнителей Deezer иногда не показывает самого известного: по «aurora» — десять безвестных Aurora,
    # а AURORA (Runaway) нет. Зато её песни есть среди треков по запросу (какие именно — зависит от страны сервера),
    # поэтому исполнителей с точно таким именем из первых треков тоже берём в кандидаты
    known = {a.get("id") for a in exact}
    leads = []
    for t in tracks:
        a = t.get("artist") or {}
        if a.get("id") and a["id"] not in known and norm(a.get("name", "")) == name_q:
            known.add(a["id"])
            leads.append(a["id"])
    if leads:
        with ThreadPoolExecutor(min(3, len(leads))) as ex:
            for found in ex.map(_artist_info, leads[:3]):
                if found:
                    exact.append(found)
    if not any((a.get("nb_fan") or 0) >= FUZZY_ARTIST_FANS for a in exact):
        # опечатка в имени («kendric lamar», «eminm»): Deezer её прощает, берём похожее имя, но только у заметных исполнителей —
        # иначе песня с похожим названием сошла бы за исполнителя. Пустые исполнители-двойники («Travis Scot») не мешают
        exact += [
            a
            for a in data.get("data", [])
            if a not in exact
            and (a.get("nb_fan") or 0) >= FUZZY_ARTIST_FANS
            and SequenceMatcher(None, norm(a["name"]), name_q).ratio() >= FUZZY_ARTIST_RATIO
        ]
    if not exact:
        return None
    # исполнителей с одним именем много — берём самого известного
    artist = max(exact, key=lambda a: a.get("nb_fan") or 0)
    if (artist.get("nb_fan") or 0) < SONG_OVER_ARTIST_FANS:
        # «believer», «blinding lights»: у малоизвестной группы то же имя, что у знаменитой песни — ищут песню
        if tracks and norm(tracks[0]["title"]) == name_q and (tracks[0].get("rank") or 0) >= SONG_MIN_RANK:
            return None
    top = get_json(f"https://api.deezer.com/artist/{artist['id']}/top?limit={n * 3}").get("data", [])
    me = norm(artist["name"])
    out: list[Canon] = []
    for t in top:
        credited = {norm(t["artist"]["name"])} | {norm(c.get("name", "")) for c in t.get("contributors", [])}
        if me not in credited:
            continue  # чужая песня, где исполнитель лишь упомянут
        title = t["title"]
        if BAD_VERSION_RE.search(title) and not BAD_VERSION_RE.search(q):
            continue  # live, remix и т.п. — не «главные» версии
        if any(norm(title) == norm(c.title) for c in out):
            continue
        out.append(
            Canon(artist["name"], title, int(t.get("duration") or 0), (t.get("album") or {}).get("cover_big", ""))
        )
        if len(out) >= n:
            break
    logging.info(
        "исполнитель %r: %s (%s поклонников, кандидатов %d, из треков %d), песен %d",
        q,
        artist["name"],
        artist.get("nb_fan"),
        len(exact),
        len(leads),
        len(out),
    )
    return (artist["name"], out) if out else None


def genius(q: str) -> list[Canon]:
    """Публичный поиск Genius: хорошо нормализует «исполнитель — название» (длительности нет)."""
    data = get_json("https://genius.com/api/search/song?per_page=5&q=" + urllib.parse.quote(q))
    hits = (data.get("response", {}).get("sections") or [{}])[0].get("hits", [])
    out = []
    for h in hits:
        r = h.get("result", {})
        artist = (r.get("primary_artist") or {}).get("name", "")
        title = r.get("title", "")
        if artist and title and "Genius" not in artist:  # пропускаем страницы переводов
            out.append(Canon(artist, title, 0, r.get("song_art_image_thumbnail_url", "")))
    return out


def yandex(q: str, n: int = 5) -> list[Canon]:
    """Яндекс Музыка как русский справочник (только метаданные и обложки): поиск открытый, файлы по подписке."""
    data = get_json(
        "https://api.music.yandex.net/search?type=track&page=0&text=" + urllib.parse.quote(q),
        headers={"X-Yandex-Music-Client": "YandexMusicAndroid/24023621"},
    )
    items = ((data.get("result") or {}).get("tracks") or {}).get("results", [])
    out = []
    for t in items[:n]:
        artists = ", ".join(a["name"] for a in t.get("artists", []) if a.get("name"))
        cover = t.get("coverUri") or ""
        out.append(
            Canon(
                artists,
                t.get("title", ""),
                int(t.get("durationMs") or 0) // 1000,
                "https://" + cover.replace("%%", "400x400") if cover else "",
            )
        )
    return [c for c in out if c.artist and c.title]


def canon_relevant(c: Canon, tokens: list[str]) -> bool:
    """Запись каталога годится, если слова запроса, не вошедшие в имя исполнителя, есть в названии.
    Так «tame impala loser» не тянет за собой другие песни Tame Impala."""
    if not tokens:
        return True
    artist_n, title_n = norm(c.artist), norm(c.title)
    rest = [t for t in tokens if t not in artist_n]
    if not rest:
        return True  # в запросе только исполнитель
    meaningful = [t for t in rest if t not in STOPWORDS] or rest  # «the», «of», «you» не считаем
    words = [norm(w) for w in re.findall(r"\w+", c.title)]

    def in_title(t: str) -> bool:  # «paolo» ~ «paulo»: опечатка в одну-две буквы не мешает
        return t in title_n or (len(t) >= 4 and any(SequenceMatcher(None, t, w).ratio() >= 0.8 for w in words if w))

    hit = sum(1 for t in meaningful if in_title(t))
    return hit / len(meaningful) >= 0.7


def dedupe_canons(parts: list[list[Canon]]) -> list[Canon]:
    out: list[Canon] = []
    for part in parts:
        for c in part:
            if not any(norm(c.title) == norm(k.title) and norm(c.artist) == norm(k.artist) for k in out):
                out.append(c)
    return out
