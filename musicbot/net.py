"""Запросы к открытым JSON API через общую сессию: соединения переиспользуются (keep-alive),
поэтому каждый запрос не тратит 200–500 мс на новое TLS-рукопожатие."""

import requests
from requests.adapters import HTTPAdapter

UA = "Mozilla/5.0 MusicBot/1.0"

session = requests.Session()
_adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32)
session.mount("https://", _adapter)
session.mount("http://", _adapter)


def get_json(url: str, timeout: int = 10, headers: dict | None = None):
    r = session.get(url, timeout=timeout, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    r.raise_for_status()
    return r.json()


def get_bytes(url: str, timeout: int = 10, headers: dict | None = None) -> bytes:
    r = session.get(url, timeout=timeout, headers={"User-Agent": UA, **(headers or {})})
    r.raise_for_status()
    return r.content
