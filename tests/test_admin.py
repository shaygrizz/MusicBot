import aiosqlite

from musicbot import db


async def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()


async def test_admin_counts_bot_and_mini_app_separately(tmp_path, monkeypatch):
    await _fresh(tmp_path, monkeypatch)
    await db.log_play(1, "A", "X", 200, "ytm", "", "audio", via="bot")
    await db.log_play(1, "B", "X", 200, "ytm", "", "audio", via="app", auto=True)
    await db.log_play(2, "C", "Y", 100, "ytm", "", "audio")  # запись до разметки источника
    await db.fav_add(1, "X", "A", via="app")
    await db.fav_add(2, "Y", "C", via="bot")

    d = await db.admin_overview(0)
    assert d["via"]["plays"] == {"bot": 1, "app": 1, "old": 1}
    assert d["via"]["favorites"] == {"bot": 1, "app": 1, "old": 0}
    assert d["totals"]["users"] == 2
    assert d["totals"]["plays"] == 3 and d["totals"]["auto"] == 1
    one = next(u for u in d["users"] if u["id"] == 1)
    assert (one["plays_bot"], one["plays_app"], one["favorites"]) == (1, 1, 1)
    assert sum(day["bot"] + day["app"] + day["old"] for day in d["daily"]) == 3

    detail = await db.admin_user(1)
    assert {p["via"] for p in detail["plays"]} == {"bot", "app"}
    assert detail["favorites"][0]["via"] == "app"


async def test_mini_app_visit_fills_the_name_of_a_user_created_without_one(tmp_path, monkeypatch):
    await _fresh(tmp_path, monkeypatch)
    await db.set_lang(5, "ru")  # строку создал выбор языка, имени в ней нет
    await db.touch_user(5, "Anna Smith", via="app")
    await db.touch_user(5, "", via="bot")  # пустое имя не стирает известное

    u = (await db.admin_overview())["users"][0]
    assert u["tg_name"] == "Anna Smith"
    assert u["last_app"] and u["last_bot"] and u["first_seen"]


async def test_first_seen_falls_back_to_the_first_play(tmp_path, monkeypatch):
    await _fresh(tmp_path, monkeypatch)
    await db.log_play(7, "A", "X", 200, "ytm", "", "audio", via="app")
    async with aiosqlite.connect(db.DB_PATH) as c:
        await c.execute("UPDATE plays SET ts=1000")
        await c.commit()
    u = (await db.admin_overview())["users"][0]
    assert u["first_seen"] == 1000 and u["tg_name"] == ""
