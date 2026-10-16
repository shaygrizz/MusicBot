"""Источники со скачиваемыми результатами: YouTube Music, YouTube, SoundCloud, Audius, hitmotop."""

import asyncio
import logging
import urllib.parse
import urllib.request

import yt_dlp

from ..download import (
    SearchResult,
    get_ytm,
    search_ytdlp_sync,
    small_thumb,
)
from ..providers import audius, hitmo

SOURCE_TIMEOUT = 8  # секунд на каждый источник, медленные отбрасываем

MIN_FULL_TRACK = 45  # короче — почти наверняка 30-секундный отрывок


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
        id=vid,
        title=e.get("title") or "Без названия",
        artist=", ".join(a["name"] for a in artists if a.get("name")),
        duration=_dur(e),
        url=f"https://www.youtube.com/watch?v={vid}",
        thumb_url=small_thumb(thumbs[-1]["url"]) if thumbs else "",
        source=tag,
    )


def search_ytm(q: str, filt: str, tag: str) -> list[SearchResult]:
    items = get_ytm().search(q, filter=filt, limit=8)
    return [r for r in (_to_result(e, tag) for e in items) if r]


def search_ytm_top(q: str) -> list[SearchResult]:
    """Поиск без фильтра: «Top result» — то, что YouTube Music считает самым точным совпадением."""
    items = get_ytm().search(q)
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


def search_audius(q: str) -> list[SearchResult]:
    return [
        SearchResult(
            id=t["id"],
            title=t["title"],
            artist=t["artist"],
            duration=t["duration"],
            url=f"audius://{t['id']}",
            thumb_url=t["cover"],
            source="au",
        )
        for t in audius.search(q, 8)
        if not t["duration"] or t["duration"] >= MIN_FULL_TRACK
    ]


def search_hitmo(q: str) -> list[SearchResult]:
    """Российский агрегатор mp3: готовые файлы с длительностью, поэтому хорошо сверяются со справочником."""
    return [
        SearchResult(
            id=t["id"],
            title=t["title"],
            artist=t["artist"],
            duration=t["duration"],
            url="hitmo://" + urllib.parse.quote(t["url"], safe=""),
            thumb_url=t["cover"],
            source="hm",
        )
        for t in hitmo.search(q, 8)
        if not t["duration"] or t["duration"] >= MIN_FULL_TRACK
    ]


def search_youtube(q: str, n: int = 5) -> list[SearchResult]:
    res = search_ytdlp_sync(q, n)
    for r in res:
        r.source = "yt"
    return res


def search_soundcloud(q: str, n: int = 8) -> list[SearchResult]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 8,
        "retries": 1,
        "extractor_retries": 1,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch{n}:{q}", download=False)
    out = []
    for e in info.get("entries", []) or []:
        url, dur = e.get("url"), int(e.get("duration") or 0)
        if not url or (dur and dur < MIN_FULL_TRACK):
            continue
        out.append(
            SearchResult(
                id=str(e.get("id") or url),
                title=e.get("title") or "Без названия",
                artist=e.get("uploader") or "",
                duration=dur,
                url=url,
                source="sc",
            )
        )
    return out


def finished(t: "asyncio.Task"):
    """Результат задачи, если она успела; иначе пусто (источник опоздал или упал)."""
    if t.done() and not t.cancelled() and t.exception() is None:
        return t.result()
    return []


async def safe_call(fn, *args):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), SOURCE_TIMEOUT)
    except Exception as e:
        logging.info("источник %s не ответил: %s", getattr(fn, "__name__", fn), str(e)[:80])
        return []
