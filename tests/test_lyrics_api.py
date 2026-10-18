"""Тексты песен: разбор LRC, подбор записи LRCLIB, кэш и ответ мини-аппа."""

from aiohttp.test_utils import TestClient, TestServer
from test_auth import TOKEN, init_data  # pytest кладёт папку tests в путь поиска модулей

from musicbot import db
from musicbot.search import lrclib
from musicbot.web import app as webapp

LRC = "[ar:Кино]\n[00:44.75] Тёплое место\n[01:00.8]Мягкое кресло\n[01:05.00]\n[00:48.4] Звёздная пыль\n"


def test_parse_lrc_sorts_lines_and_keeps_pauses():
    assert lrclib.parse_lrc(LRC) == [
        [44.75, "Тёплое место"],
        [48.4, "Звёздная пыль"],
        [60.8, "Мягкое кресло"],
        [65.0, ""],
    ]
    assert lrclib.parse_lrc("просто текст") == []


def hit(**kw):
    return {"trackName": "Song", "artistName": "Artist", "duration": 200, "instrumental": False,
            "syncedLyrics": LRC, "plainLyrics": "a\nb", **kw}  # fmt: skip


def test_synced_lines_are_dropped_when_the_recording_is_much_longer_or_shorter():
    assert lrclib._shape(hit(), 201)["synced"]
    far = lrclib._shape(hit(duration=260), 200)
    assert far["synced"] is None and far["plain"] == "a\nb"  # строки уехали бы от звука
    assert lrclib._shape(hit(instrumental=True), 200) is None
    assert lrclib._shape(hit(syncedLyrics=None, plainLyrics=None), 200) is None


def test_lookup_falls_back_to_search_and_prefers_synced(monkeypatch):
    calls = []

    def request(path, params):
        calls.append(path)
        if path == "get":
            return None
        return [
            hit(syncedLyrics=None, trackName="Song (Radio Edit)"),
            hit(trackName="Song - Remastered"),
            hit(trackName="Another", artistName="Artist"),
            hit(trackName="Song", artistName="Someone Else"),
        ]

    monkeypatch.setattr(lrclib, "_request", request)
    found = lrclib.lookup("Artist feat. Guest", "Song (feat. Guest)", 200)
    assert found["synced"] and calls == ["get", "get", "search"]  # целиком и только главный артист, потом поиск


def test_title_and_artist_are_cleaned_for_the_query():
    assert lrclib._names("Artist, Guest", "Song (feat. Guest) - Remastered 2011") == (
        ["Artist, Guest", "Artist"],
        "Song",
    )


async def test_lyrics_are_cached_and_misses_are_cached_too(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()
    asked = []

    def lookup(artist, title, duration=0):
        asked.append(title)
        return {"synced": None, "plain": "text"} if title == "Has" else None

    monkeypatch.setattr(lrclib, "lookup", lookup)
    assert (await lrclib.get_lyrics("A", "Has", 200)) == {"synced": None, "plain": "text"}
    assert (await lrclib.get_lyrics("A", "Has", 201)) == {"synced": None, "plain": "text"}  # та же запись: из кэша
    assert await lrclib.get_lyrics("A", "None", 200) is None
    assert await lrclib.get_lyrics("A", "None", 200) is None
    assert asked == ["Has", "None"]


async def test_endpoint_needs_signed_data_and_reports_found_or_not(monkeypatch):
    async def get_lyrics(artist, title, duration=0):
        if title == "Boom":
            raise RuntimeError("503")
        return {"synced": [[1.0, "x"]], "plain": "x"} if title == "Has" else None

    monkeypatch.setattr(webapp.lrclib, "get_lyrics", get_lyrics)
    c = TestClient(TestServer(webapp.make_app(None, TOKEN, "test_bot", set())))
    await c.start_server()
    try:
        head = {"X-Init-Data": init_data()}
        assert (await c.post("/api/lyrics", json={"title": "Has"})).status == 401
        r = await c.post("/api/lyrics", json={"artist": "A", "title": "Has", "duration": 200}, headers=head)
        assert await r.json() == {"found": True, "synced": [[1.0, "x"]], "plain": "x"}
        r = await c.post("/api/lyrics", json={"artist": "A", "title": "No"}, headers=head)
        assert await r.json() == {"found": False}
        assert (await c.post("/api/lyrics", json={"artist": "A", "title": "Boom"}, headers=head)).status == 502
        assert (await c.post("/api/lyrics", json={"artist": "A"}, headers=head)).status == 400
    finally:
        await c.close()
