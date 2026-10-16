"""Российский бесплатный источник: сайт-агрегатор mp3 rus.hitmotop.com.

Это не API, а разбор страницы поиска, поэтому может перестать работать, если сайт поменяет разметку.
Сайт не лицензирован правообладателями, он служит запасным источником, когда песни нет в других местах.
"""

import json
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

BASE = "https://rus.hitmotop.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": BASE + "/",
}


def _absolute(url: str) -> str:
    """У части треков сайт отдаёт путь без адреса (/get/music/...): достраиваем до полной ссылки."""
    return BASE + url if url.startswith("/") else url


def _seconds(text: str) -> int:
    parts = text.strip().split(":")
    if not parts or not all(p.isdigit() for p in parts):
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + int(p)
    return sec


def search(query: str, limit: int = 8) -> list[dict]:
    """Список треков: artist, title, duration, url (лёгкая версия 128 кбит/с), cover."""
    req = urllib.request.Request(f"{BASE}/search?q={urllib.parse.quote(query)}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=12) as r:
        soup = BeautifulSoup(r.read().decode("utf-8", "ignore"), "html.parser")
    out = []
    for el in soup.select("li.tracks__item"):
        try:
            meta = json.loads(el.get("data-musmeta") or "{}")
        except ValueError:
            continue
        if not meta.get("url") or not meta.get("title"):
            continue
        dur = el.select_one(".track__fulltime")
        out.append(
            {
                "id": str(meta.get("id") or meta["url"][-24:]),
                "artist": (meta.get("artist") or "").strip(),
                "title": meta["title"].strip(),
                "duration": _seconds(dur.text) if dur else 0,
                "url": _absolute(meta["url"]),
                "cover": meta.get("img") or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def download(url: str, dest) -> None:
    """Скачивает файл; при сбое сети повторяет с нарастающей паузой."""
    url = _absolute(url)
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
                data = r.read()
            if len(data) < 100_000:
                raise RuntimeError("hitmo: файл слишком маленький")
            dest.write_bytes(data)
            return
        except Exception as e:
            last = e
            time.sleep(0.5 * 2**attempt)
    raise last  # type: ignore[misc]
