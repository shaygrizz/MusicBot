"""Запросы к открытым JSON API."""

import json
import urllib.parse
import urllib.request


def get_json(url: str, timeout: int = 10, headers: dict | None = None):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 MusicBot/1.0", "Accept": "application/json", **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)
