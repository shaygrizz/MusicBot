"""Мини-апп: песни из Top tracks, Recent и списка исполнителя включаются по нажатию."""

import urllib.parse

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


async def test_wave_needs_signed_data_and_returns_a_batch(monkeypatch):
    seen_args = {}

    def fake_wave(taste, seen, skipped, n=10):
        seen_args.update(seen=seen, skipped=skipped)
        return [{"artist": "Кино", "title": "Кукушка", "duration": 400, "cover": "", "because": "Кино"}]

    async def fake_taste(user_id, limit=20):
        return [("Кино", 3)]

    monkeypatch.setattr(webapp.db, "taste_artists", fake_taste)
    monkeypatch.setattr(webapp, "_deezer_wave", fake_wave)
    c = await client()
    try:
        assert (await c.post("/api/wave", json={})).status == 401
        r = await c.post("/api/wave", json={"seen": ["a|b"], "skipped": ["ABBA"]}, headers={"X-Init-Data": init_data()})
        assert r.status == 200
        assert [i["title"] for i in (await r.json())["items"]] == ["Кукушка"]
        assert seen_args["seen"] == {"a|b"} and seen_args["skipped"] == {
            "abba"
        }  # исполнители приводятся к нижнему регистру
    finally:
        await c.close()


def test_wave_skips_played_songs_and_skipped_artists(monkeypatch):
    tracks = {
        "Muse": [{"artist": {"name": "Muse"}, "title": "Hysteria", "duration": 227, "album": {}}],
        "Abba": [{"artist": {"name": "Abba"}, "title": "Waterloo", "duration": 170, "album": {}}],
    }

    def fake_json(url):
        if "search/artist" in url:
            name = urllib.parse.unquote(url.rsplit("q=", 1)[1])
            return {"data": [{"id": name}]}
        if url.endswith("/top?limit=25"):
            return {"data": tracks[url.split("/artist/")[1].split("/")[0]]}
        return {"data": []}

    monkeypatch.setattr(webapp, "_get_json", fake_json)
    monkeypatch.setattr(webapp, "_dz_artist", {})
    monkeypatch.setattr(webapp, "_dz_related", {})
    seen = {webapp.db.fav_key("Muse", "Hysteria")}
    out = webapp._deezer_wave([("Muse", 5), ("Abba", 1)], seen, {"abba"})
    assert out == []  # Muse уже играла, Abba пропущена
    out = webapp._deezer_wave([("Muse", 5), ("Abba", 1)], set(), {"abba"})
    assert {t["title"] for t in out} == {"Hysteria"}
