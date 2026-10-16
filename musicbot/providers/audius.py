"""Audius — открытая музыкальная платформа (независимые артисты, ремиксы). API бесплатный, без ключа.
Документация: https://docs.audius.org"""

import json
import urllib.parse
import urllib.request

HOST = "https://api.audius.co"
APP = "MusicBot"


def _get(url: str, timeout: int = 12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MusicBot/1.0", "Accept": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def search(query: str, limit: int = 6) -> list[dict]:
    q = urllib.parse.urlencode({"query": query, "limit": limit, "app_name": APP})
    with _get(f"{HOST}/v1/tracks/search?{q}") as r:
        data = json.load(r).get("data", [])
    out = []
    for t in data:
        if t.get("is_streamable") is False or t.get("is_delete"):
            continue
        art = t.get("artwork") or {}
        out.append(
            {
                "id": t["id"],
                "title": t.get("title") or "Без названия",
                "artist": (t.get("user") or {}).get("name", ""),
                "duration": int(t.get("duration") or 0),
                "cover": art.get("480x480") or art.get("150x150") or "",
            }
        )
    return out


def stream_url(track_id: str) -> str:
    return f"{HOST}/v1/tracks/{track_id}/stream?app_name={APP}"


def download(track_id: str, dest) -> None:
    with _get(stream_url(track_id), timeout=40) as r:
        data = r.read()
    if len(data) < 50_000:
        raise RuntimeError("Audius отдал слишком маленький файл")
    dest.write_bytes(data)
