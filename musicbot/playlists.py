"""Плейлисты и альбомы по ссылке: YouTube, YouTube Music, Spotify. Достаём только список песен
(исполнитель, название, длительность, ссылка на ролик, если она есть); качаются песни обычным путём, по одной."""

import json
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass, field

import yt_dlp

from . import net
from .download import get_ytm
from .download.youtube import youtube_opts

MAX_TRACKS = int(os.getenv("PLAYLIST_MAX", "200"))  # больше за раз не читаем
SPOTIFY_EMBED_LIMIT = 100  # встраиваемая страница Spotify отдаёт не больше стольких песен

# автомиксы (RD…) бесконечны, «Понравившиеся» (LL, LM) и «Смотреть позже» (WL) — личные и без входа пусты
EXCLUDED_LISTS = ("RD", "LL", "WL", "LM", "UL")
LIST_RE = re.compile(r"^[\w-]{10,}$")
SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?:intl-[\w-]+/)?(?:embed/)?(playlist|album)/([A-Za-z0-9]{22})")
YTM_ALBUM_RE = re.compile(r"/browse/(MPRE[\w-]+)")
BRACKETS_RE = re.compile(
    r"\s*[(\[](?:[^)\]]*\b(?:official|video|audio|lyrics?|visuali[sz]er|clip|mv|hd|4k|клип|премьера)\b[^)\]]*)[)\]]",
    re.I,
)


@dataclass
class Track:
    artist: str
    title: str
    duration: int
    url: str = ""  # ролик YouTube, если известен: качаем его без поиска
    cover: str = ""

    def as_item(self) -> dict:
        return {
            "artist": self.artist,
            "title": self.title,
            "duration": self.duration,
            "url": self.url,
            "cover": self.cover,
        }


@dataclass
class Playlist:
    title: str
    source: str  # youtube / spotify
    tracks: list[Track] = field(default_factory=list)
    cut: bool = False  # источник отдал не весь список


def _list_id(url: str) -> str | None:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    lid = (q.get("list") or [""])[0]
    if not LIST_RE.match(lid) or lid.startswith(EXCLUDED_LISTS):
        return None
    return lid


def classify(url: str) -> tuple[str, str] | None:
    """Ссылка на плейлист или альбом: ("youtube", id списка), ("ytm_album", browseId), ("spotify:playlist", id),
    ("spotify_short", ссылка). Ссылка на одну песню из плейлиста (watch?v=…&list=…) сюда не попадает."""
    p = urllib.parse.urlparse(url)
    host, path = p.netloc.lower(), p.path
    if host.endswith(("spotify.link", "spoti.fi")):
        return ("spotify_short", url)
    m = SPOTIFY_RE.search(url)
    if m:
        return (f"spotify:{m.group(1)}", m.group(2))
    if "youtube.com" in host or "youtu.be" in host:
        m = YTM_ALBUM_RE.search(path)
        if m:
            return ("ytm_album", m.group(1))
        if path.rstrip("/") == "/playlist":
            lid = _list_id(url)
            return ("youtube", lid) if lid else None
    return None


def playlist_of_single(url: str) -> str | None:
    """Песня, которой поделились из плейлиста (watch?v=…&list=…): сама ссылка — на песню, но можно взять и весь список."""
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    if ("youtube.com" in host and p.path == "/watch") or "youtu.be" in host:
        return _list_id(url)
    return None


def clean_youtube_title(raw: str, channel: str) -> tuple[str, str]:
    """«Shakira, Burna Boy - Dai Dai (Official Video)» с канала «Shakira» -> («Shakira, Burna Boy», «Dai Dai»)."""
    title = BRACKETS_RE.sub("", raw).strip()
    artist = channel.removesuffix(" - Topic").strip()
    if " - " in title:
        left, right = title.split(" - ", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    return artist, title


def _artists(entry: dict) -> str:
    return ", ".join(a["name"] for a in entry.get("artists") or [] if a.get("name"))


def _ytm_tracks(entries: list[dict]) -> list[Track]:
    out = []
    for e in entries:
        if not e.get("title"):
            continue
        # ролик недоступен (удалён, закрыт в стране сервера) — песню не выбрасываем, а ищем по названию
        vid = e.get("videoId") if e.get("isAvailable") is not False else None
        thumbs = e.get("thumbnails") or []
        out.append(
            Track(
                artist=_artists(e),
                title=e["title"],
                duration=int(e.get("duration_seconds") or 0),
                url=f"https://www.youtube.com/watch?v={vid}" if vid else "",
                cover=thumbs[-1]["url"] if thumbs else "",
            )
        )
    return out


def _youtube_flat(list_id: str) -> Playlist:
    """Запасной путь: yt-dlp читает любой плейлист YouTube без скачивания; названия «сырые», их чистим."""
    opts = youtube_opts(
        {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "playlistend": MAX_TRACKS}
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/playlist?list={list_id}", download=False)
    tracks = []
    for e in info.get("entries") or []:
        if not e or not e.get("id") or not e.get("duration"):  # [Private video], [Deleted video]
            continue
        artist, title = clean_youtube_title(e.get("title") or "", e.get("channel") or e.get("uploader") or "")
        tracks.append(Track(artist, title, int(e["duration"]), f"https://www.youtube.com/watch?v={e['id']}"))
    return Playlist(info.get("title") or "YouTube playlist", "youtube", tracks)


def read_youtube(list_id: str) -> Playlist:
    ytm = get_ytm()
    if list_id.startswith("OLAK5uy_"):  # альбом: у YouTube Music по нему чистые названия
        try:
            browse = ytm.get_album_browse_id(list_id)
            if browse:
                return read_ytm_album(browse)
        except Exception as e:
            logging.info("альбом YouTube Music не прочитан (%s): %s", list_id, str(e)[:120])
    try:
        p = ytm.get_playlist(list_id, limit=MAX_TRACKS)
        tracks = _ytm_tracks(p.get("tracks") or [])
        if tracks:
            return Playlist(p.get("title") or "YouTube playlist", "youtube", tracks[:MAX_TRACKS])
    except Exception as e:
        logging.info("плейлист YouTube Music не прочитан (%s), пробуем yt-dlp: %s", list_id, str(e)[:120])
    return _youtube_flat(list_id)


def read_ytm_album(browse_id: str) -> Playlist:
    a = get_ytm().get_album(browse_id)
    tracks = _ytm_tracks(a.get("tracks") or [])
    album_artist = _artists(a)
    thumbs = a.get("thumbnails") or []
    for t in tracks:
        t.artist = t.artist or album_artist
        t.cover = t.cover or (thumbs[-1]["url"] if thumbs else "")
    title = f"{album_artist} - {a.get('title')}" if album_artist else a.get("title") or "Album"
    return Playlist(title, "youtube", tracks[:MAX_TRACKS])


def read_spotify(kind: str, sid: str) -> Playlist:
    """Встраиваемая страница Spotify отдаёт список без ключа разработчика (до 100 песен)."""
    html = net.get_bytes(f"https://open.spotify.com/embed/{kind}/{sid}", timeout=15).decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html)
    if not m:
        raise RuntimeError("Spotify: на странице нет списка песен")
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Spotify изменил страницу: список песен не найден")
    covers = (entity.get("coverArt") or {}).get("sources") or []
    cover = covers[0]["url"] if covers else ""
    tracks = []
    for t in entity.get("trackList") or []:
        # подкасты и выпуски шоу — не песни. «Недоступна» (isPlayable False) относится к Spotify в стране сервера,
        # а не к YouTube: такие песни оставляем, иначе они молча пропадали бы из плейлиста
        if not t.get("title") or (t.get("entityType") or "track") != "track":
            continue
        artist = ", ".join(a.strip() for a in (t.get("subtitle") or "").replace("\xa0", " ").split(",") if a.strip())
        tracks.append(Track(artist, t["title"], int(t.get("duration") or 0) // 1000, "", cover))
    name = entity.get("name") or entity.get("title") or "Spotify"
    if kind == "album" and entity.get("subtitle"):
        name = f"{entity['subtitle'].replace(chr(160), ' ')} - {name}"
    return Playlist(name, "spotify", tracks[:MAX_TRACKS], cut=len(entity.get("trackList") or []) >= SPOTIFY_EMBED_LIMIT)


def resolve_spotify_short(url: str) -> tuple[str, str] | None:
    """spotify.link / spoti.fi ведут на open.spotify.com через переадресацию или страницу со ссылкой."""
    r = net.session.get(url, timeout=10, headers={"User-Agent": net.UA}, allow_redirects=True)
    for text in (r.url, r.text[:200_000]):
        m = SPOTIFY_RE.search(text)
        if m:
            return (f"spotify:{m.group(1)}", m.group(2))
    return None


def read(kind: str, ident: str) -> Playlist:
    """Синхронно (ходит в сеть): вызывать через asyncio.to_thread."""
    if kind == "spotify_short":
        found = resolve_spotify_short(ident)
        if not found:
            raise RuntimeError("короткая ссылка Spotify не ведёт на плейлист или альбом")
        kind, ident = found
    if kind == "youtube":
        return read_youtube(ident)
    if kind == "ytm_album":
        return read_ytm_album(ident)
    if kind.startswith("spotify:"):
        return read_spotify(kind.split(":", 1)[1], ident)
    raise ValueError(f"неизвестный тип плейлиста: {kind}")
