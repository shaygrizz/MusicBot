"""Тексты песен из LRCLIB (lrclib.net): открытая база без ключа, у многих песен есть тайм-коды строк."""

import asyncio
import json
import logging
import re
import time
import urllib.parse

from .. import db, net
from ..textutil import core_title, norm, same_artist

API = "https://lrclib.net/api"
UA = "MusicBot (https://github.com/shaygrizz/MusicBot)"  # LRCLIB просит представляться
FOUND_TTL = 30 * 86400  # найденный текст не меняется
MISSING_TTL = 2 * 86400  # «текста нет» проверяем снова: его могли добавить
SYNC_TOLERANCE = 8  # секунд: тайм-коды другой по длине записи не совпадут со звуком
TIMEOUT = 8

_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_GUEST = re.compile(r"\s*[(\[]\s*(?:feat|ft|with)\b.*?[)\]]", re.I)
_SPLIT_ARTIST = re.compile(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b)\s*", re.I)


def parse_lrc(text: str) -> list[list]:
    """«[00:44.75] строка» -> [[44.75, "строка"], ...] по времени. Пустая строка — пауза между куплетами."""
    lines: list[list] = []
    for raw in text.splitlines():
        stamps = list(_STAMP.finditer(raw))
        if not stamps:
            continue  # заголовки вроде [ar:Артист] и пустые строки
        words = raw[stamps[-1].end() :].strip()
        for m in stamps:
            frac = int(m.group(3).ljust(3, "0")[:3]) / 1000 if m.group(3) else 0
            lines.append([round(int(m.group(1)) * 60 + int(m.group(2)) + frac, 2), words])
    lines.sort(key=lambda line: line[0])
    return lines


def _request(path: str, params: dict):
    """JSON ответа; None, если LRCLIB не знает песню (404). Сбой сервера один раз повторяем."""
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        r = net.session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        if r.status_code == 404:
            return None
        if r.status_code >= 500 and attempt == 0:
            time.sleep(0.6)
            continue
        r.raise_for_status()
        return r.json()


def _shape(hit: dict, duration: int) -> dict | None:
    """Запись LRCLIB -> {"synced": [[сек, строка]...] | None, "plain": текст | None}; пусто, если текста нет."""
    if not hit or hit.get("instrumental"):
        return None
    synced = parse_lrc(hit.get("syncedLyrics") or "") or None
    if synced and duration and hit.get("duration") and abs(hit["duration"] - duration) > SYNC_TOLERANCE:
        synced = None  # другая запись: строки уехали бы от звука, остаётся обычный текст
    plain = (hit.get("plainLyrics") or "").strip() or None
    if plain is None and hit.get("syncedLyrics"):
        plain = "\n".join(line for _, line in parse_lrc(hit["syncedLyrics"])).strip() or None
    return {"synced": synced, "plain": plain} if synced or plain else None


def _names(artist: str, title: str) -> tuple[list[str], str]:
    """Как артиста и название пишет LRCLIB: без «feat.» и пометок каталога; артист целиком и только главный."""
    clean = _GUEST.sub("", core_title(title)).strip() or title
    artists = [artist.strip()]
    first = _SPLIT_ARTIST.split(artist, 1)[0].strip()
    if first and first != artists[0]:
        artists.append(first)
    return [a for a in artists if a], clean


def lookup(artist: str, title: str, duration: int = 0) -> dict | None:
    """Текст песни: точный запрос по длине, иначе поиск среди записей с тем же названием и исполнителем."""
    artists, clean = _names(artist, title)
    for name in artists:
        params = {"artist_name": name, "track_name": clean}
        if duration:
            params["duration"] = duration
        found = _shape(_request("get", params) or {}, duration)
        if found:
            return found
    want = norm(clean)
    best, best_key = None, None
    for hit in _request("search", {"track_name": clean, "artist_name": artists[0]}) or []:
        if norm(_GUEST.sub("", core_title(hit.get("trackName") or ""))) != want:
            continue
        if not same_artist(artists[0], hit.get("artistName") or ""):
            continue
        shaped = _shape(hit, duration)
        if not shaped:
            continue
        gap = abs((hit.get("duration") or 0) - duration) if duration and hit.get("duration") else 0
        key = (shaped["synced"] is None, gap)  # сначала с тайм-кодами, потом ближайшая по длине
        if best_key is None or key < best_key:
            best, best_key = shaped, key
    return best


def _key(artist: str, title: str, duration: int) -> str:
    return f"lyrics|{norm(artist)}|{norm(core_title(title))}|{duration // 5}"


async def get_lyrics(artist: str, title: str, duration: int = 0) -> dict | None:
    """Текст из кэша (SQLite) или из LRCLIB. None — текста нет. Сбой сети поднимается наверх и в кэш не попадает."""
    key = _key(artist, title, duration)
    cached = await db.get_search(key, FOUND_TTL)
    if cached is not None:
        data = json.loads(cached)
        if data is not None or await db.get_search(key, MISSING_TTL) is not None:
            return data
    data = await asyncio.wait_for(asyncio.to_thread(lookup, artist, title, duration), TIMEOUT * 3)
    await db.set_search(key, json.dumps(data, ensure_ascii=False))
    logging.info("текст песни %s - %s: %s", artist, title, "найден" if data else "не найден")
    return data
