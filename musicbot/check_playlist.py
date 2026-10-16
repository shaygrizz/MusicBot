"""Проверка плейлиста без скачивания: для каждой песни подбирает версию и печатает, что не нашлось и почему.

Запускать на сервере, с его IP (в консоли Bothost, одной строкой):
    python -m musicbot.check_playlist https://open.spotify.com/playlist/...

Сами песни не качаются, поэтому YouTube почти не нагружается. Значки в выводе:
    ✓ версия найдена   ~ найдена с мягкой проверкой длины   ✗ не найдена (ниже — что было в выдаче и почему не подошло)
"""

import asyncio
import logging
import sys

from . import db, playlists, search
from .download import SearchResult

PARALLEL = 2  # больше — источники не успевают отвечать, и проверка показывает ложные пропуски


async def check(url: str) -> int:
    found = playlists.classify(url)
    if not found:
        print("Это не ссылка на плейлист или альбом (YouTube, YouTube Music, Spotify).")
        return 2
    await db.init_db()
    pl = await asyncio.to_thread(playlists.read, *found)
    print(f"{pl.title}: {len(pl.tracks)} песен" + (" (список обрезан источником)" if pl.cut else ""))
    slots = asyncio.Semaphore(PARALLEL)

    async def one(t: playlists.Track):
        if t.url:  # ссылка на ролик уже есть: подбирать нечего, песня качается по ней
            return t, "link", []
        meta = SearchResult(id="", title=t.title, artist=t.artist, duration=t.duration, url=search.lazy_for(
            t.artist, t.title, t.duration))  # fmt: skip
        why: list = []
        async with slots:
            try:
                got = await search.resolve(meta, why=why)
            except Exception as e:
                return t, "error", [(None, f"ошибка поиска: {str(e)[:100]}")]
        if not got:
            return t, "miss", why
        return t, "loose" if got[0].loose else "ok", []

    results = await asyncio.gather(*(one(t) for t in pl.tracks))
    marks = {"ok": "✓", "link": "✓", "loose": "~", "miss": "✗", "error": "✗"}
    for i, (t, state, why) in enumerate(results, 1):
        if state in ("ok", "link"):
            continue
        print(f"{marks[state]} {i}. {t.artist} - {t.title} ({t.duration} с)")
        for r, reason in why:
            print(f"      {r.artist} - {r.title} ({r.duration} с): {reason}" if r else f"      {reason}")
        if state == "miss" and not why:
            print("      в выдаче ничего похожего")
    counts = {s: sum(1 for _, st, _ in results if st == s) for s in marks}
    print(
        f"\nНайдено {counts['ok'] + counts['link'] + counts['loose']} из {len(results)}"
        f" (с мягкой проверкой длины: {counts['loose']}), не найдено: {counts['miss'] + counts['error']}"
    )
    return 0


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) != 2:
        print("Использование: python -m musicbot.check_playlist <ссылка на плейлист>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(check(sys.argv[1])))


if __name__ == "__main__":
    main()
