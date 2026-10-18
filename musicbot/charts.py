"""Чарты страны для новых пользователей: страна определяется по часовому поясу из браузера (Telegram страну не отдаёт),
чарт берётся из открытой ленты Apple Music (без ключа). Нужен, пока у человека нет своего вкуса."""

import logging
import time

from . import net

FEED = "https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{n}/songs.json"
CHART_SIZE = 50
TTL = 6 * 3600  # чарты Apple обновляются раз в день
RETRY_AFTER = 2 * 60  # лента не ответила: до этого срока не спрашиваем снова
FEED_TIMEOUT = 15  # лента Apple бывает медленной: Италия с 10 песнями отвечала дольше 20 с, с 50 за секунды
_cache: dict[str, tuple[float, list[dict]]] = {}

# часовой пояс браузера -> страна (ISO 3166-1); пояса, которых здесь нет, определяются по языку интерфейса
_ZONES = {
    "RU": "Europe/Moscow Europe/Kaliningrad Europe/Samara Europe/Volgograd Europe/Kirov Europe/Saratov Europe/Astrakhan "
    "Europe/Ulyanovsk Asia/Yekaterinburg Asia/Omsk Asia/Novosibirsk Asia/Barnaul Asia/Tomsk Asia/Novokuznetsk "
    "Asia/Krasnoyarsk Asia/Irkutsk Asia/Chita Asia/Yakutsk Asia/Vladivostok Asia/Magadan Asia/Sakhalin "
    "Asia/Srednekolymsk Asia/Kamchatka Asia/Anadyr",
    "IT": "Europe/Rome Europe/Vatican Europe/San_Marino",
    "UA": "Europe/Kyiv Europe/Kiev Europe/Uzhgorod Europe/Zaporozhye",
    "BY": "Europe/Minsk",
    "KZ": "Asia/Almaty Asia/Qostanay Asia/Aqtobe Asia/Aqtau Asia/Oral",
    "GE": "Asia/Tbilisi",
    "AM": "Asia/Yerevan",
    "AZ": "Asia/Baku",
    "UZ": "Asia/Tashkent Asia/Samarkand",
    "MD": "Europe/Chisinau",
    "LV": "Europe/Riga",
    "LT": "Europe/Vilnius",
    "EE": "Europe/Tallinn",
    "PL": "Europe/Warsaw",
    "CZ": "Europe/Prague",
    "SK": "Europe/Bratislava",
    "HU": "Europe/Budapest",
    "RO": "Europe/Bucharest",
    "BG": "Europe/Sofia",
    "RS": "Europe/Belgrade",
    "HR": "Europe/Zagreb",
    "GR": "Europe/Athens",
    "TR": "Europe/Istanbul Asia/Istanbul",
    "DE": "Europe/Berlin Europe/Busingen",
    "AT": "Europe/Vienna",
    "CH": "Europe/Zurich",
    "FR": "Europe/Paris",
    "ES": "Europe/Madrid Atlantic/Canary",
    "PT": "Europe/Lisbon Atlantic/Azores Atlantic/Madeira",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "SE": "Europe/Stockholm",
    "NO": "Europe/Oslo",
    "DK": "Europe/Copenhagen",
    "FI": "Europe/Helsinki",
    "IL": "Asia/Jerusalem Asia/Tel_Aviv",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "EG": "Africa/Cairo",
    "ZA": "Africa/Johannesburg",
    "NG": "Africa/Lagos",
    "IN": "Asia/Kolkata Asia/Calcutta",
    "PK": "Asia/Karachi",
    "TH": "Asia/Bangkok",
    "ID": "Asia/Jakarta",
    "PH": "Asia/Manila",
    "SG": "Asia/Singapore",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "AU": "Australia/Sydney Australia/Melbourne Australia/Brisbane Australia/Perth Australia/Adelaide",
    "NZ": "Pacific/Auckland",
    "US": "America/New_York America/Chicago America/Denver America/Los_Angeles America/Phoenix America/Anchorage "
    "Pacific/Honolulu America/Detroit America/Boise",
    "CA": "America/Toronto America/Vancouver America/Montreal America/Edmonton America/Winnipeg America/Halifax",
    "MX": "America/Mexico_City America/Monterrey America/Tijuana",
    "BR": "America/Sao_Paulo America/Fortaleza America/Bahia America/Recife America/Manaus America/Belem",
    "AR": "America/Argentina/Buenos_Aires America/Buenos_Aires",
    "CO": "America/Bogota",
    "CL": "America/Santiago",
    "PE": "America/Lima",
}
COUNTRY_OF_ZONE = {zone: code for code, zones in _ZONES.items() for zone in zones.split()}
COUNTRY_OF_LANG = {"ru": "RU", "it": "IT", "en": "US"}


def country_for(timezone: str | None, lang: str) -> str:
    """Страна пользователя: по часовому поясу браузера, иначе по языку интерфейса."""
    return COUNTRY_OF_ZONE.get((timezone or "").strip()) or COUNTRY_OF_LANG.get(lang, "US")


def _fetch(country: str) -> list[dict]:
    data = net.get_json(FEED.format(country=country.lower(), n=CHART_SIZE), timeout=FEED_TIMEOUT)
    out = []
    for t in data.get("feed", {}).get("results", []):
        if not t.get("name") or not t.get("artistName"):
            continue
        cover = (t.get("artworkUrl100") or "").replace("/100x100", "/300x300")
        out.append(
            {"artist": t["artistName"], "title": t["name"], "duration": 0, "cover": cover, "because": "", "auto": True}
        )
    return out


def country_chart(country: str) -> list[dict]:
    """Чарт страны (до 50 песен, в порядке чарта) из кэша или ленты Apple; пусто, если лента не ответила."""
    hit = _cache.get(country)
    if hit and time.time() < hit[0]:
        return hit[1]
    try:
        chart = _fetch(country)
    except Exception as e:
        logging.info("чарт страны %s не получен: %s", country, str(e)[:80])
        chart = []
    _cache[country] = (time.time() + (TTL if chart else RETRY_AFTER), chart)
    return chart
