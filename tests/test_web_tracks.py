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

    def fake_wave(taste, seen, skipped, n=10, avoid=None):
        seen_args.update(seen=seen, skipped=skipped)
        return [{"artist": "Кино", "title": "Кукушка", "duration": 400, "cover": "", "because": "Кино"}]

    async def fake_taste(user_id, limit=20):
        return [("Кино", 3)]

    async def nobody_disliked(user_id):
        return set()

    async def no_known_songs(user_id):
        return []

    monkeypatch.setattr(webapp.db, "taste_artists", fake_taste)
    monkeypatch.setattr(webapp.db, "disliked_artists", nobody_disliked)
    monkeypatch.setattr(webapp.db, "known_songs", no_known_songs)
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


async def test_taste_ignores_auto_plays_and_one_off_plays(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.db, "DB_PATH", str(tmp_path / "t.db"))
    await webapp.db.init_db()
    log = webapp.db.log_play
    await log(1, "Song", "Once", 200, "ytm", "u", "audio")  # одно прослушивание: ещё не вкус
    await log(1, "A", "Twice", 200, "ytm", "u", "audio")
    await log(1, "B", "Twice", 200, "ytm", "u", "audio")
    for i in range(5):  # песни, которые подобрала волна, вкус не определяют
        await log(1, f"W{i}", "Brazil", 200, "ytm", "u", "audio", auto=True)
    await webapp.db.fav_add(1, "Fav", "Loved", 200)
    assert await webapp.db.taste_artists(1) == [("Fav", 3), ("Twice", 2)]


async def test_new_user_gets_seed_artists_in_own_language(monkeypatch):
    got = {}

    async def no_taste(user_id, limit=20):
        return []

    def fake_wave(taste, seen, skipped, n=10, avoid=None):
        got["taste"] = taste
        return []

    async def nobody_disliked(user_id):
        return set()

    async def no_known_songs(user_id):
        return []

    monkeypatch.setattr(webapp.db, "taste_artists", no_taste)
    monkeypatch.setattr(webapp.db, "disliked_artists", nobody_disliked)
    monkeypatch.setattr(webapp.db, "known_songs", no_known_songs)
    monkeypatch.setattr(webapp, "_deezer_wave", fake_wave)
    c = await client()
    try:
        await c.post("/api/wave", json={}, headers={"X-Init-Data": init_data()})
    finally:
        await c.close()
    assert {a for a, _ in got["taste"]} <= set(webapp.SEED_ARTISTS["en"]) | set(webapp.SEED_ARTISTS["ru"]) | set(
        webapp.SEED_ARTISTS["it"]
    )
    assert got["taste"]


async def test_skipped_song_weighs_less_and_old_taste_fades(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.db, "DB_PATH", str(tmp_path / "t.db"))
    await webapp.db.init_db()
    log, db = webapp.db.log_play, webapp.db
    for i in range(2):  # два пропуска за секунды: вкусом ещё не стали
        play = await log(1, f"S{i}", "Skipped", 200, "ytm", "u", "audio")
        await db.set_listened(1, play, 5)
    for i in range(2):  # два прослушивания до конца
        play = await log(1, f"H{i}", "Heard", 200, "ytm", "u", "audio")
        await db.set_listened(1, play, 190)
    assert [a for a, _ in await db.taste_artists(1)] == ["Heard"]
    async with db.aiosqlite.connect(db.DB_PATH) as conn:  # год назад: вес падает, но не до нуля
        await conn.execute("UPDATE plays SET ts = ts - 365*86400 WHERE artist = 'Heard'")
        await conn.commit()
    ((artist, weight),) = await db.taste_artists(1)
    assert artist == "Heard" and 0.5 < weight < 1


async def test_dislike_hides_an_artist_for_a_month(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.db, "DB_PATH", str(tmp_path / "t.db"))
    await webapp.db.init_db()
    db = webapp.db
    await db.dislike_artist(1, "Brazil Funk")
    assert await db.disliked_artists(1) == {"brazil funk"}
    assert await db.disliked_artists(2) == set()
    await db.dislike_artist(1, "Old", days=-1)  # срок вышел
    assert "old" not in await db.disliked_artists(1)


def test_related_artists_in_a_foreign_script_are_left_out():
    allowed = webapp._allowed_scripts([("Скриптонит", 5), ("Zivert", 1)])
    assert allowed == {"cyr", "lat"}
    assert webapp._allowed_scripts([("Скриптонит", 5), ("Баста", 3)]) == {"cyr"}
    only_cyr = webapp._allowed_scripts([("Скриптонит", 5)])
    assert webapp._script_ok("Баста", only_cyr) and not webapp._script_ok("宇多田ヒカル", only_cyr)
    assert not webapp._script_ok("Anitta", only_cyr)
    assert webapp._script_ok("Anitta", None)


def test_wave_does_not_play_another_version_of_a_known_song(monkeypatch):
    top = [
        {"artist": {"name": "Muse"}, "title": "Hysteria (Radio Edit)", "duration": 227, "album": {}},
        {"artist": {"name": "Muse"}, "title": "Hysteria - Remastered 2011", "duration": 227, "album": {}},
        {"artist": {"name": "Muse"}, "title": "Starlight", "duration": 240, "album": {}},
    ]

    def fake_json(url):
        if "search/artist" in url:
            return {"data": [{"id": 1}]}
        if url.endswith("/top?limit=25"):
            return {"data": top}
        return {"data": []}

    monkeypatch.setattr(webapp, "_get_json", fake_json)
    monkeypatch.setattr(webapp, "_dz_artist", {})
    monkeypatch.setattr(webapp, "_dz_related", {})
    monkeypatch.setattr(webapp, "WAVE_RELATED_CHANCE", 0)
    avoid = webapp._song_index([("Muse, Someone", "Hysteria")])  # в избранном: два исполнителя и короткое название
    for _ in range(20):
        out = webapp._deezer_wave([("Muse", 3)], set(), set(), 3, avoid)
        assert {t["title"] for t in out} == {"Starlight"}


def test_song_versions_match_loosely_but_other_songs_do_not():
    index = webapp._song_index([("Земфира", "Хочу")])
    assert webapp._known_song(index, "Zemfira", "Хочу (Live)")
    assert not webapp._known_song(index, "Zemfira", "Прости меня моя любовь")
    assert not webapp._known_song(index, "Другой", "Хочу")


async def test_favorite_keeps_the_exact_file_and_source_the_user_heard(monkeypatch, tmp_path):
    from musicbot import favcache

    asked = fake_fetch(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp.db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(favcache, "CACHE_DIR", tmp_path / "fav")
    await webapp.db.init_db()
    ensured = []

    async def ensure(user_id, artist, title, duration=0, cover="", url=""):
        ensured.append(url)
        return "tok", tmp_path / "x.m4a"

    c = await client()
    try:
        head = {"X-Init-Data": init_data()}
        song = {"artist": "Muse", "title": "Hysteria", "duration": 227}  # волна: у песни нет своей ссылки
        r = await c.post("/api/track", json=song, headers=head)
        assert r.status == 200
        lazy = asked[0]
        assert lazy.startswith("lazy://")
        r = await c.post("/api/favorites", json={**song, "op": "add"}, headers=head)
        assert r.status == 200
        assert asked == [lazy]  # заново ничего не подбиралось
        token = favcache.token_for(42, "Muse", "Hysteria")
        assert favcache.find(token).read_bytes() == b"audio"  # тот самый файл, что играл
        assert await webapp.db.fav_url(42, "Muse", "Hysteria") == lazy  # и его источник запомнен

        monkeypatch.setattr(favcache, "ensure", ensure)
        r = await c.post("/api/favplay", json=song, headers=head)
        assert r.status == 200 and ensured == [lazy]  # при повторной загрузке качается тот же источник
    finally:
        await c.close()
