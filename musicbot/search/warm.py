"""Прогрев источников поиска. Первый запрос к YouTube Music и каталогам после простоя тратит 1–2 с
на создание клиента и TLS-соединения; держим их тёплыми, чтобы поиск у пользователя шёл по готовым соединениям."""

import asyncio
import logging
import os

from ..download import get_ytm
from .http import get_json

WARM_INTERVAL = float(os.getenv("WARM_INTERVAL", "45"))  # 0 — только один прогрев при запуске


def _ping() -> None:
    get_ytm().get_search_suggestions("a")
    get_json("https://api.deezer.com/search?limit=1&q=a", timeout=5)
    get_json("https://itunes.apple.com/search?entity=song&limit=1&term=a", timeout=5)


async def _ping_safe() -> None:
    try:
        await asyncio.wait_for(asyncio.to_thread(_ping), 15)
    except Exception as e:
        logging.info("прогрев источников не удался: %s", str(e)[:80])


async def keep_warm() -> None:
    await _ping_safe()
    while WARM_INTERVAL > 0:
        await asyncio.sleep(WARM_INTERVAL)
        await _ping_safe()
