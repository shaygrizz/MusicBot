import time

import aiosqlite

from musicbot import db


async def _play_at(ts, duration, listened=None):
    play = await db.log_play(42, "Song", "Artist", duration, "ytm", "", "track")
    async with aiosqlite.connect(db.DB_PATH) as c:
        await c.execute("UPDATE plays SET ts=?, listened=? WHERE id=?", (ts, listened, play))
        await c.commit()


async def test_days_follow_the_users_clock_and_count_what_was_heard(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()
    midnight = int(time.time()) // 86400 * 86400  # полночь UTC сегодня
    late = midnight - 90 * 60  # 22:30 UTC вчера = 01:30 сегодня в Москве
    await _play_at(late, 200)  # дослушана: listened не пришёл, считается длина
    await _play_at(late, 200, listened=5)  # пропущена через 5 секунд
    day = lambda ts: time.strftime("%Y-%m-%d", time.gmtime(ts))  # noqa: E731

    utc = await db.dashboard(42, 0)
    assert utc["days"] == {day(late): 2}
    assert utc["day_seconds"] == {day(late): 205}
    assert utc["hours"] == {22: 2}

    moscow = await db.dashboard(42, 180)
    assert moscow["days"] == {day(midnight): 2}
    assert moscow["day_seconds"] == {day(midnight): 205}
    assert moscow["hours"] == {1: 2}
