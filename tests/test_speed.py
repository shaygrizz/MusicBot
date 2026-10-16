"""Кэши и общие загрузки, на которых держится скорость: повторное не качается и не ищется второй раз."""

import asyncio

from musicbot import audiocache
from musicbot.download import Downloaded
from musicbot.search import engine, fetch
from musicbot.search.models import Outcome
from musicbot.web import app as webapp


def downloaded(tmp_path, name="a"):
    wd = tmp_path / f"wd-{name}"
    wd.mkdir()
    path = wd / f"{name}.m4a"
    path.write_bytes(b"audio-" + name.encode())
    return Downloaded(path=path, thumb=None, title="x", artist="y", duration=200, source="Youtube", workdir=wd)


async def test_song_downloaded_once_comes_from_the_disk_cache(monkeypatch, result, tmp_path):
    monkeypatch.setattr(fetch, "DOWNLOAD_DIR", tmp_path / "downloads")
    meta = result("Song", "Artist", 200)
    calls = []

    async def fake_download(url, kind, meta=None, fallbacks=True):
        calls.append(url)
        return downloaded(tmp_path, str(len(calls)))

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 200)
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    first = await fetch.fetch_audio(url, meta)
    first.cleanup()  # временная папка удалена, а кэш остаётся
    second = await fetch.fetch_audio(url, meta)
    assert calls == [url]
    assert second.path.read_bytes() == b"audio-1"
    assert (second.title, second.artist, second.source) == ("Song", "Artist", "cache")
    second.cleanup()
    assert audiocache.path_for(url) is not None  # копия из кэша не трогает сам кэш


async def test_catalog_song_is_cached_under_the_link_it_was_found_by(monkeypatch, result, tmp_path):
    monkeypatch.setattr(fetch, "DOWNLOAD_DIR", tmp_path / "downloads")
    meta = result("Song", "Artist", 200)

    async def fake_download(url, kind, meta=None, fallbacks=True):
        return downloaded(tmp_path)

    async def one_version(meta, tried, limit):
        return [result("Song", "Artist", 200, url="https://www.youtube.com/watch?v=found")]

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "resolve", one_version)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 200)
    await fetch.fetch_audio("lazy://Artist%7CSong%7C200", meta)
    assert audiocache.path_for("lazy://Artist%7CSong%7C200") is not None
    assert audiocache.path_for("https://www.youtube.com/watch?v=found") is not None


def test_search_outcome_survives_the_database_round_trip(result):
    out = Outcome([result("Song", "Artist", 200, url="https://x")], "hint", "q", kind="artist", artist="A")
    out.results[0].exact = True
    back = engine._load(engine._dump(out))
    assert back == out


async def test_repeated_search_is_answered_from_the_database_after_restart(monkeypatch, result):
    stored = {}

    async def get_search(key, max_age):
        return stored.get(key)

    async def set_search(key, data, keep=5000):
        stored[key] = data

    calls = []

    async def impl(query, limit, deep, fast_only=False):
        calls.append(query)
        return Outcome([result("Song", "Artist", 200, url="https://x")], "", query)

    monkeypatch.setattr(engine.db, "get_search", get_search)
    monkeypatch.setattr(engine.db, "set_search", set_search)
    monkeypatch.setattr(engine, "_search_impl", impl)
    monkeypatch.setattr(engine, "_search_cache", {})
    await engine.search("artist song")
    engine._search_cache.clear()  # «перезапуск»: память пуста, база осталась
    again = await engine.search("artist song")
    assert calls == ["artist song"]
    assert again.results[0].url == "https://x"


async def test_same_search_running_twice_at_once_is_done_once(monkeypatch, result):
    async def nothing(*args, **kwargs):
        return None

    calls = []

    async def impl(query, limit, deep, fast_only=False):
        calls.append(query)
        await asyncio.sleep(0.05)
        return Outcome([result("Song", "Artist", 200, url="https://x")], "", query)

    monkeypatch.setattr(engine.db, "get_search", nothing)
    monkeypatch.setattr(engine.db, "set_search", nothing)
    monkeypatch.setattr(engine, "_search_impl", impl)
    monkeypatch.setattr(engine, "_search_cache", {})
    a, b = await asyncio.gather(engine.search("artist song"), engine.search("artist song"))
    assert calls == ["artist song"] and a is b


async def test_prefetch_and_play_share_one_download(monkeypatch, result, tmp_path):
    calls = []

    async def fake_fetch(url, meta):
        calls.append(url)
        await asyncio.sleep(0.05)
        return downloaded(tmp_path)

    monkeypatch.setattr(webapp.search, "fetch_audio", fake_fetch)
    monkeypatch.setattr(webapp, "files", {})
    monkeypatch.setattr(webapp, "inflight", {})
    r = result("Song", "Artist", 200, url="https://www.youtube.com/watch?v=abcdefghijk")
    (t1, d1), (t2, d2) = await asyncio.gather(webapp._get_file(1, "v", r), webapp._get_file(1, "v", r))
    assert calls == [r.url]
    assert t1 == t2 and d1 is d2
    assert webapp._ready_file(1, "v")[0] == t1


def test_early_exit_needs_an_exact_track_near_the_top(result):
    exact = result("Song", "Artist", 200, url="https://1")
    exact.exact = True
    others = [result(f"Other {i}", "Artist", 200, url=f"https://{i + 2}") for i in range(3)]
    assert engine._good_enough([exact, *others[:2]])
    assert not engine._good_enough(others)  # без точного трека ждём медленные источники
    assert not engine._good_enough([exact])  # слишком мало, чтобы выбирать
