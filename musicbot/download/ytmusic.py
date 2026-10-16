"""Поиск через YouTube Music и yt-dlp."""

import asyncio
import re

import yt_dlp

from .models import SearchResult
from .youtube import youtube_opts

_ytm = None


def get_ytm():
    global _ytm
    if _ytm is None:
        import requests
        from requests.adapters import HTTPAdapter
        from ytmusicapi import YTMusic

        # поиск открывает к YouTube Music больше 10 соединений сразу; со стандартным пулом лишние выбрасываются
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=32)
        session.mount("https://", adapter)
        _ytm = YTMusic(requests_session=session)
    return _ytm


def small_thumb(url: str) -> str:
    # googleusercontent-обложки умеют отдавать нужный размер
    return re.sub(r"=w\d+-h\d+.*$", "=w320-h320-l90-rj", url) if "=w" in url else url


def _search_ytm_sync(query: str, limit: int) -> list[SearchResult]:
    ytm = get_ytm()
    items = ytm.search(query, filter="songs", limit=limit)
    if not items:
        items = ytm.search(query, filter="videos", limit=limit)
    results = []
    for e in items[:limit]:
        vid = e.get("videoId")
        if not vid:
            continue
        artists = e.get("artists") or []
        thumbs = e.get("thumbnails") or []
        results.append(
            SearchResult(
                id=vid,
                title=e.get("title") or "Без названия",
                artist=", ".join(a["name"] for a in artists if a.get("name")),
                duration=int(e.get("duration_seconds") or 0),
                url=f"https://www.youtube.com/watch?v={vid}",
                thumb_url=small_thumb(thumbs[-1]["url"]) if thumbs else "",
            )
        )
    return results


def search_ytdlp_sync(query: str, limit: int) -> list[SearchResult]:
    opts = youtube_opts({"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for e in info.get("entries", []) or []:
        if not e or not e.get("id"):
            continue
        results.append(
            SearchResult(
                id=e["id"],
                title=e.get("title") or "Без названия",
                artist=e.get("uploader") or e.get("channel") or "",
                duration=int(e.get("duration") or 0),
                url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
            )
        )
    return results


def _search_sync(query: str, limit: int) -> list[SearchResult]:
    try:
        res = _search_ytm_sync(query, limit)
        if res:
            return res
    except Exception:
        pass
    return search_ytdlp_sync(query, limit)


async def search(query: str, limit: int = 6) -> list[SearchResult]:
    return await asyncio.to_thread(_search_sync, query, limit)
