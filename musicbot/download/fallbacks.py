"""Запасные источники по названию: SoundCloud, hitmotop, Audius, dydki, tikwm."""

import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yt_dlp

from ..textutil import BAD_VERSION_RE
from .media import MAX_LEADING_SILENCE, leading_silence
from .models import SearchResult
from .urls import YT_ID_RE
from .youtube import ytdlp_run
from .ytmusic import get_ytm


def _sc_candidates(query: str, meta: SearchResult | None) -> list[str]:
    """Ссылки на SoundCloud: сначала подходящие по длительности и без «странных» версий."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 10,
        "retries": 1,
        "extractor_retries": 1,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch8:{query}", download=False)
    artist_tokens = [_n(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_n(t)) >= 3] if meta else []
    scored = []
    for e in info.get("entries", []) or []:
        link, title = e.get("url"), e.get("title") or ""
        if not link:
            continue
        if BAD_VERSION_RE.search(title) and not BAD_VERSION_RE.search(query):
            continue
        if artist_tokens and not any(t in _n(f"{e.get('uploader') or ''} {title}") for t in artist_tokens):
            continue  # песня с таким же названием, но другого исполнителя: лучше ничего, чем чужая
        dur = e.get("duration")
        if meta and meta.duration and dur and abs(dur - meta.duration) > 10:
            continue
        diff = abs(dur - meta.duration) if (meta and meta.duration and dur) else 5
        scored.append((diff, link))
    scored.sort(key=lambda x: x[0])
    return [link for _, link in scored[:3]]


def _soundcloud(query: str, kind: str, workdir: Path, meta: SearchResult | None):
    last_err = "на SoundCloud нет подходящей версии"
    extra = {"socket_timeout": 10, "retries": 1, "extractor_retries": 1}
    for link in _sc_candidates(query, meta):
        try:
            path, info = ytdlp_run(link, kind, workdir, meta, extra)
            gap = leading_silence(path)
            if gap > MAX_LEADING_SILENCE:
                last_err = f"версия начинается с {gap:.0f} с тишины"
                path.unlink(missing_ok=True)
                continue
            return path, info
        except Exception as e:
            last_err = str(e)[:150]
    raise RuntimeError(last_err)


def tikwm(url: str, kind: str, workdir: Path):
    """Запасной источник для TikTok, когда сам TikTok просит войти в аккаунт."""
    import json

    api = "https://www.tikwm.com/api/?hd=1&url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.load(r)
    data = resp.get("data") or {}
    if resp.get("code") != 0 or not data:
        raise RuntimeError(f"tikwm: {resp.get('msg') or 'пустой ответ'}")

    music = data.get("music_info") or {}
    if kind == "audio":
        media_url = data.get("music") or music.get("play")
        suffix = ".audio"  # формат неизвестен — _finish прогонит через ffmpeg в mp3
    else:
        media_url = data.get("hdplay") or data.get("play")
        suffix = ".mp4"
    if not media_url:
        raise RuntimeError("tikwm: нет ссылки на файл")

    path = workdir / f"{data.get('id', 'tiktok')}{suffix}"
    req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        path.write_bytes(r.read())
    author = data.get("author") or {}
    info = {
        "id": str(data.get("id", "")),
        "title": (data.get("title") or "TikTok")[:200],
        "track": music.get("title") if kind == "audio" else None,
        "artist": music.get("author") if kind == "audio" else None,
        "uploader": author.get("nickname") or author.get("unique_id") or "",
        "duration": int(music.get("duration") or 0) if kind == "audio" else int(data.get("duration") or 0),
        "extractor_key": "TikTok",
    }
    return path, info


DYDKI_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _n(s: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\(.*?\)|\[.*?\]", " ", s.lower()))


def _dydki(meta: SearchResult, workdir: Path):
    """Самый последний запасной источник: сайт-агрегатор готовых mp3 (по мотивам TelegramMusicBot).
    Метаданные там шумные, поэтому берём только строгое совпадение названия и исполнителя."""
    from bs4 import BeautifulSoup

    query = f"{meta.artist} {meta.title}".strip()
    req = urllib.request.Request("https://dydki.net/?mp3=" + urllib.parse.quote(query), headers=DYDKI_UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        soup = BeautifulSoup(r.read().decode("utf-8", "ignore"), "html.parser")
    box = soup.find("div", class_="results")
    if box is None:
        raise RuntimeError("dydki: нет результатов")
    want_title, want_artist = _n(meta.title), [_n(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_n(t)) >= 3]
    asked_bad = bool(BAD_VERSION_RE.search(query))
    best = None
    for el in box.find_all("div", class_="chkd"):
        a, t, mp3 = el.find(class_="track__artist"), el.find(class_="track__title"), el.get("data-mp3")
        if not (a and t and isinstance(mp3, str)):
            continue
        title_text, artist_text = t.text.strip(), a.text.strip()
        if want_title and want_title not in _n(title_text):
            continue
        if want_artist and not any(w in _n(artist_text + title_text) for w in want_artist):
            continue
        if BAD_VERSION_RE.search(title_text) and not asked_bad:
            continue
        best = mp3
        break
    if not best:
        raise RuntimeError("dydki: нет подходящей версии")
    path = workdir / "dydki.mp3"
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(best, headers=DYDKI_UA), timeout=60) as r:
                data = r.read()
            if len(data) < 100_000:
                raise RuntimeError("dydki: файл слишком маленький")
            path.write_bytes(data)
            return path, {"id": "dydki", "extractor_key": "dydki"}
        except Exception as e:
            last = e
            time.sleep(0.5 * 2**attempt)
    raise last  # type: ignore[misc]


def _sc_query(url: str, meta: SearchResult | None) -> str | None:
    """Строка для поиска в SoundCloud: из метаданных поиска или из YouTube Music по id."""
    if meta and meta.title:
        return f"{meta.artist} {meta.title}".strip()
    m = YT_ID_RE.search(url)
    if m:
        try:
            d = get_ytm().get_song(m.group(1)).get("videoDetails", {})
            if d.get("title"):
                return f"{d.get('author', '')} {d['title']}".strip()
        except Exception:
            pass
    return None


def audius_download(url: str, kind: str, workdir: Path):
    from ..providers import audius

    if kind != "audio":
        raise RuntimeError("Audius — только аудио")
    track_id = url.removeprefix("audius://")
    path = workdir / f"{track_id}.mp3"
    audius.download(track_id, path)
    return path, {"id": track_id, "extractor_key": "Audius"}


def hitmo_download(url: str, kind: str, workdir: Path):
    from ..providers import hitmo

    if kind != "audio":
        raise RuntimeError("hitmo: только аудио")
    path = workdir / "hitmo.mp3"
    hitmo.download(urllib.parse.unquote(url.removeprefix("hitmo://")), path)
    return path, {"id": "hitmo", "extractor_key": "hitmo"}


def _hitmo_by_title(meta: SearchResult, workdir: Path):
    """Запасной путь: ищем трек на hitmotop по названию и исполнителю, берём совпадение по названию и длительности."""
    from ..providers import hitmo

    asked_bad = bool(BAD_VERSION_RE.search(meta.title))
    want = _n(meta.title)
    artist_tokens = [_n(t) for t in re.split(r"[\s,&]+", meta.artist) if len(_n(t)) >= 3]
    for t in hitmo.search(f"{meta.artist} {meta.title}".strip(), 12):
        text = _n(t["artist"] + t["title"])
        if want and want not in _n(t["title"]):
            continue
        if artist_tokens and not any(a in text for a in artist_tokens):
            continue
        if meta.duration and t["duration"] and abs(t["duration"] - meta.duration) > 8:
            continue
        if BAD_VERSION_RE.search(t["title"]) and not asked_bad:
            continue
        return hitmo_download("hitmo://" + urllib.parse.quote(t["url"], safe=""), "audio", workdir)
    raise RuntimeError("hitmo: нет подходящей версии")


def title_fallbacks(url: str, kind: str, meta: SearchResult | None):
    """Запасные способы по названию (не по ссылке): SoundCloud, hitmotop, затем dydki."""
    query = _sc_query(url, meta)
    if kind == "audio" and query:
        yield "soundcloud", (lambda wd: _soundcloud(query, kind, wd, meta)), False
    if kind == "audio" and meta and meta.title:
        yield "hitmo", (lambda wd: _hitmo_by_title(meta, wd)), False
    if kind == "audio" and meta and meta.title and meta.artist:
        yield "dydki", (lambda wd: _dydki(meta, wd)), False  # последний шанс
