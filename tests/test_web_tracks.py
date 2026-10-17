"""Мини-апп: песни из Top tracks, Recent и списка исполнителя включаются по нажатию."""

from aiohttp.test_utils import TestClient, TestServer
from test_auth import TOKEN, init_data  # pytest кладёт папку tests в путь поиска модулей

from musicbot.download import Downloaded
from musicbot.search.models import Outcome
from musicbot.web import app as webapp


async def client():
    c = TestClient(TestServer(webapp.make_app(None, TOKEN, "test_bot", set())))
    await c.start_server()
    return c


def fake_fetch(monkeypatch, tmp_path):
    asked = []

    async def fetch_audio(url, meta):
        asked.append(url)
        wd = tmp_path / f"wd{len(asked)}"
        wd.mkdir()
        p = wd / "a.m4a"
        p.write_bytes(b"audio")
        return Downloaded(path=p, thumb=None, title=meta.title, artist=meta.artist, duration=200, source="Youtube",
                          workdir=wd)  # fmt: skip

    async def nothing(*a, **k):
        return 1

    monkeypatch.setattr(webapp.search, "fetch_audio", fetch_audio)
    monkeypatch.setattr(webapp.db, "log_play", nothing)
    monkeypatch.setattr(webapp, "files", {})
    monkeypatch.setattr(webapp, "inflight", {})
    return asked


async def test_track_from_the_dashboard_plays_by_its_youtube_link(monkeypatch, tmp_path):
    asked = fake_fetch(monkeypatch, tmp_path)
    c = await client()
    try:
        body = {"artist": "Кино", "title": "Спокойная ночь", "duration": 368,
                "url": "https://www.youtube.com/watch?v=abcdefghijk"}  # fmt: skip
        r = await c.post("/api/track", json=body, headers={"X-Init-Data": init_data()})
        assert r.status == 200
        url = (await r.json())["url"]
        assert asked == ["https://www.youtube.com/watch?v=abcdefghijk"]
        audio = await c.get(url)
        assert audio.status == 200 and await audio.read() == b"audio"
    finally:
        await c.close()


async def test_page_cannot_make_the_server_download_an_arbitrary_link(monkeypatch, tmp_path):
    asked = fake_fetch(monkeypatch, tmp_path)
    c = await client()
    try:
        body = {"artist": "Кино", "title": "Спокойная ночь", "duration": 368, "url": "https://evil.example/file"}
        r = await c.post("/api/track", json=body, headers={"X-Init-Data": init_data()})
        assert r.status == 200
        assert asked[0].startswith("lazy://")  # чужая ссылка заменена поиском по названию
    finally:
        await c.close()


async def test_new_endpoints_require_signed_telegram_data():
    c = await client()
    try:
        assert (await c.post("/api/track", json={"title": "x"})).status == 401
        assert (await c.get("/api/artist?name=Kino")).status == 401
    finally:
        await c.close()


async def test_artist_songs_are_listed_to_pick_from(monkeypatch, result):
    async def fake_search(query, limit=8, deep=False, fast_only=False, fresh=False):
        return Outcome(
            [result("Группа крови", "Кино", 285, url="lazy://a"), result("Кукушка", "Кино", 400, url="lazy://b")],
            kind="artist",
            artist="Кино",
        )

    monkeypatch.setattr(webapp.search, "search", fake_search)
    c = await client()
    try:
        r = await c.get("/api/artist?name=Kino", headers={"X-Init-Data": init_data()})
        data = await r.json()
        assert data["artist"] == "Кино"
        assert [i["title"] for i in data["items"]] == ["Группа крови", "Кукушка"]
    finally:
        await c.close()
