"""Тексты песен: LRCLIB (lrclib.net) — открытая база без ключа, у многих песен есть тайм-коды строк; если там пусто,
обычный текст берётся из lyrics.ovh."""

import asyncio
import json
import logging
import re
import time
import urllib.parse
from difflib import SequenceMatcher

from .. import db, net
from ..textutil import core_title, norm, same_artist

API = "https://lrclib.net/api"
OVH = "https://api.lyrics.ovh/v1"
MIN_PLAIN = 40  # символов: короче — это не текст песни, а заглушка
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


def _title_key(title: str) -> str:
    return norm(_GUEST.sub("", core_title(title))).replace("ё", "е")


def _same_title(a: str, b: str) -> bool:
    x, y = _title_key(a), _title_key(b)
    return x == y or (min(len(x), len(y)) >= 6 and SequenceMatcher(None, x, y).ratio() >= 0.9)


def _pick(hits, artists: list[str], title: str, duration: int) -> dict | None:
    """Лучшая запись среди найденных: то же название и исполнитель (нестрого: «Земфира»/«Zemfira», «A, B»/«A»),
    сначала с тайм-кодами, потом ближайшая по длине."""
    best, best_key = None, None
    for hit in hits or []:
        if not _same_title(hit.get("trackName") or "", title):
            continue
        if not any(same_artist(a, hit.get("artistName") or "") for a in artists):
            continue
        shaped = _shape(hit, duration)
        if not shaped:
            continue
        gap = abs((hit.get("duration") or 0) - duration) if duration and hit.get("duration") else 0
        key = (shaped["synced"] is None, gap)
        if best_key is None or key < best_key:
            best, best_key = shaped, key
    return best


def _lrclib(artist: str, title: str, duration: int) -> dict | None:
    """Точный запрос по длине, потом поиск: по полям, по общей строке и по одному названию (исполнитель
    сверяется уже в ответе, так находятся песни, записанные под другим написанием имени)."""
    artists, clean = _names(artist, title)
    titles = [clean] + ([clean.replace("ё", "е").replace("Ё", "Е")] if "ё" in clean.lower() else [])
    exact = [(name, clean) for name in artists] + [(artists[0], t) for t in titles[1:]]
    for name, track in exact:
        params = {"artist_name": name, "track_name": track}
        if duration:
            params["duration"] = duration
        found = _shape(_request("get", params) or {}, duration)
        if found:
            return found
    for track in titles:
        for params in (
            {"track_name": track, "artist_name": artists[0]},
            {"q": f"{artists[0]} {track}"},
            {"track_name": track},
        ):
            found = _pick(_request("search", params), artists, clean, duration)
            if found:
                return found
    return None


def _ovh(artist: str, title: str) -> dict | None:
    """Запасной источник: только обычный текст, без тайм-кодов. Сбой этого сервиса не должен ронять поиск."""
    artists, clean = _names(artist, title)
    url = f"{OVH}/{urllib.parse.quote(artists[-1], safe='')}/{urllib.parse.quote(clean, safe='')}"
    try:
        r = net.session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        if r.status_code != 200:
            return None
        text = str(r.json().get("lyrics") or "").replace("\r\n", "\n").strip()
    except Exception as e:
        logging.info("lyrics.ovh не ответил для %s - %s: %s", artist, title, str(e)[:80])
        return None
    return {"synced": None, "plain": text} if len(text) >= MIN_PLAIN else None


def lookup(artist: str, title: str, duration: int = 0) -> dict | None:
    """Текст песни: сначала LRCLIB (с тайм-кодами), если там нет — lyrics.ovh (обычный текст). Если LRCLIB не ответил,
    запасной источник всё равно пробуем, а когда и он пуст, сбой поднимается наверх: «нет текста» в кэш не попадёт."""
    failure = None
    try:
        found = _lrclib(artist, title, duration)
    except Exception as e:
        failure, found = e, None
    found = found or _ovh(artist, title)
    if found is None and failure is not None:
        raise failure
    return found


def _key(artist: str, title: str, duration: int) -> str:
    return f"lyrics2|{norm(artist)}|{norm(core_title(title))}|{duration // 5}"  # «2»: прежние «не найдено» не считаются


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
