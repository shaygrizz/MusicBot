"""Разбор ссылок: короткие адреса, обёртки, id роликов YouTube."""

import re
import urllib.parse
import urllib.request

URL_RE = re.compile(r"https?://\S+")


def unwrap_url(url: str) -> str:
    """Достаёт настоящую ссылку из обёрток вроде tiktok.com/login?redirect_url=... и чистит трекинг."""
    for _ in range(3):
        p = urllib.parse.urlparse(url)
        host = p.netloc.lower()
        if "tiktok.com" in host and p.path.startswith("/login"):
            target = urllib.parse.parse_qs(p.query).get("redirect_url")
            if not target:
                break
            url = target[0]
            continue
        break
    p = urllib.parse.urlparse(url)
    if "tiktok.com" in p.netloc.lower() and "/video/" in p.path:
        url = urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))  # без _r, _t и т.п.
    return url


def resolve_short(url: str) -> str:
    """Короткие ссылки TikTok (vm./vt./ /t/) разворачиваем в полные."""
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    tiktok_short = host.endswith("tt.site") or (
        "tiktok.com" in host and (host.split(".")[0] in ("vm", "vt") or p.path.startswith("/t/"))
    )
    if tiktok_short:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return unwrap_url(r.geturl())
        except Exception:
            pass
    return url


def extract_url(text: str) -> str | None:
    m = URL_RE.search(text)
    return unwrap_url(m.group(0).rstrip(").,")) if m else None


YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})")
