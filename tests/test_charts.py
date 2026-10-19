"""Чарты страны для новичков: страна по часовому поясу и ленте Apple."""

from musicbot import charts

FEED = {
    "feed": {
        "results": [
            {"artistName": "Zivert", "name": "Credo", "artworkUrl100": "https://x/100x100bb.jpg"},
            {"artistName": "", "name": "Без исполнителя"},
            {"artistName": "Мот", "name": "Капли"},
        ]
    }
}


def test_country_comes_from_the_time_zone_then_from_the_language():
    assert charts.country_for("Europe/Moscow", "en") == "RU"
    assert charts.country_for("Asia/Vladivostok", "en") == "RU"
    assert charts.country_for("Europe/Rome", "ru") == "IT"
    assert charts.country_for("Antarctica/Troll", "ru") == "RU"  # пояса нет в таблице: язык интерфейса
    assert charts.country_for(None, "it") == "IT"
    assert charts.country_for("", "xx") == "US"


def test_chart_is_parsed_cached_and_a_failure_is_not_asked_again_at_once(monkeypatch):
    monkeypatch.setattr(charts, "_cache", {})
    calls = []

    def get_json(url, timeout=10, headers=None):
        calls.append(url)
        if "/xx/" in url:
            raise RuntimeError("504")
        return FEED

    monkeypatch.setattr(charts.net, "get_json", get_json)
    chart = charts.country_chart("RU")
    assert [t["title"] for t in chart] == ["Credo", "Капли"]  # запись без исполнителя отброшена
    assert chart[0]["cover"] == "https://x/300x300bb.jpg" and chart[0]["auto"] is True
    assert charts.country_chart("RU") == chart and len(calls) == 1
    assert charts.country_chart("XX") == [] and charts.country_chart("XX") == [] and len(calls) == 2


def test_world_chart_comes_from_deezer(monkeypatch):
    monkeypatch.setattr(charts, "_cache", {})
    deezer = {
        "data": [
            {
                "title": "APT.",
                "duration": 170,
                "artist": {"name": "ROSÉ"},
                "album": {"cover_medium": "https://d/250x250.jpg"},
            },
            {"title": "", "artist": {"name": "Пусто"}},
        ]
    }
    monkeypatch.setattr(charts.net, "get_json", lambda url, timeout=10, headers=None: deezer if "deezer" in url else {})
    chart = charts.country_chart(charts.GLOBAL)
    assert chart == [
        {
            "artist": "ROSÉ",
            "title": "APT.",
            "duration": 170,
            "cover": "https://d/250x250.jpg",
            "because": "",
            "auto": True,
        }
    ]


async def test_mini_app_asks_for_a_country_or_the_world_chart(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from test_auth import TOKEN, init_data

    from musicbot import db
    from musicbot.web.app import make_app

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()
    asked = []
    monkeypatch.setattr(charts, "country_chart", lambda c: asked.append(c) or [{"title": str(i)} for i in range(30)])
    c = TestClient(TestServer(make_app(None, TOKEN, "test_bot", set())))
    await c.start_server()
    try:
        head = {"X-Init-Data": init_data(user={"id": 42, "first_name": "Rese"})}
        world = await (await c.get("/api/chart?country=global", headers=head)).json()
        assert world["country"] == "global" and len(world["items"]) == 10
        assert (await (await c.get("/api/chart?country=it", headers=head)).json())["country"] == "IT"
        assert (await c.get("/api/chart?country=../x", headers=head)).status == 400
        assert (await c.get("/api/chart?country=US")).status == 401
        assert asked == ["global", "IT"]
    finally:
        await c.close()
