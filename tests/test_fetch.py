import pytest

from musicbot.download import Downloaded
from musicbot.search import fetch


def downloaded(tmp_path, duration=200):
    path = tmp_path / "a.m4a"
    path.write_bytes(b"x" * 10)
    return Downloaded(
        path=path, thumb=None, title="x", artist="y", duration=duration, source="Youtube", workdir=tmp_path
    )


async def test_file_of_the_wrong_length_is_rejected(monkeypatch, result, tmp_path):
    meta = result("Song", "Artist", 200)

    async def fake_download(url, kind, meta=None, fallbacks=True):
        if url == "title://":
            raise RuntimeError("no fallback")
        return downloaded(tmp_path)

    async def no_versions(*args, **kwargs):
        return []

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "resolve", no_versions)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 90)  # скачался отрывок вместо песни
    with pytest.raises(RuntimeError, match="длительность"):
        await fetch.fetch_audio("https://www.youtube.com/watch?v=abcdefghijk", meta)


async def test_file_of_the_right_length_gets_catalog_name(monkeypatch, result, tmp_path):
    meta = result("Song", "Artist", 200)

    async def fake_download(url, kind, meta=None, fallbacks=True):
        return downloaded(tmp_path)

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 203)
    d = await fetch.fetch_audio("https://www.youtube.com/watch?v=abcdefghijk", meta)
    assert (d.title, d.artist) == ("Song", "Artist")


async def test_other_version_is_tried_when_the_chosen_one_fails(monkeypatch, result, tmp_path):
    meta = result("Song", "Artist", 200)
    asked = []

    async def fake_download(url, kind, meta=None, fallbacks=True):
        asked.append(url)
        if url == "https://bad":
            raise RuntimeError("403")
        return downloaded(tmp_path)

    async def one_version(meta, tried, limit):
        return [result("Song", "Artist", 200, url="https://good")]

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "resolve", one_version)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 200)
    await fetch.fetch_audio("https://bad", meta)
    assert asked == ["https://bad", "https://good"]


YT = "https://www.youtube.com/watch?v=abcdefghijk"


async def test_blocked_youtube_goes_to_fallbacks_without_searching_more_youtube_links(monkeypatch, result, tmp_path):
    meta = result("Song", "Artist", 200)
    asked = []

    async def fake_download(url, kind, meta=None, fallbacks=True, ignore_block=False):
        asked.append(url)
        return downloaded(tmp_path)

    async def no_resolve(*args, **kwargs):
        raise AssertionError("поиск других версий на YouTube при блокировке не нужен")

    monkeypatch.setattr(fetch, "youtube_block_left", lambda: 60.0)
    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "resolve", no_resolve)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 200)
    await fetch.fetch_audio(YT, meta)
    assert asked == ["title://"]


async def test_blocked_youtube_gets_a_last_chance_after_the_fallbacks_fail(monkeypatch, result, tmp_path):
    meta = result("Song", "Artist", 200)
    asked = []

    async def fake_download(url, kind, meta=None, fallbacks=True, ignore_block=False):
        asked.append((url, ignore_block))
        if url == "title://":
            raise RuntimeError("SoundCloud: нет подходящей версии")
        return downloaded(tmp_path)

    monkeypatch.setattr(fetch, "youtube_block_left", lambda: 60.0)
    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 200)
    await fetch.fetch_audio(YT, meta)
    assert asked == [("title://", False), (YT, True)]


async def test_error_names_both_youtube_and_fallback_reasons(monkeypatch, result):
    meta = result("Song", "Artist", 200)

    async def fake_download(url, kind, meta=None, fallbacks=True, ignore_block=False):
        raise RuntimeError(
            "hitmo: нет подходящей версии" if url == "title://" else "Sign in to confirm you’re not a bot"
        )

    async def no_versions(*args, **kwargs):
        return []

    monkeypatch.setattr(fetch, "youtube_block_left", lambda: 0.0)
    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "resolve", no_versions)
    with pytest.raises(RuntimeError) as err:
        await fetch.fetch_audio(YT, meta)
    assert "not a bot" in str(err.value) and "hitmo" in str(err.value)


async def test_catalog_song_gets_a_youtube_last_chance_when_the_fallbacks_fail(monkeypatch, result, tmp_path):
    """У песни из каталога (избранное, плейлист, страница исполнителя) ссылки на YouTube ещё нет: ищем её в последний момент."""
    meta = result("Song", "Artist", 200)
    asked = []

    async def fake_download(url, kind, meta=None, fallbacks=True, ignore_block=False):
        asked.append((url, ignore_block))
        if url == "title://":
            raise RuntimeError("hitmo: нет подходящей версии")
        return downloaded(tmp_path)

    async def one_version(meta, tried, limit):
        return [result("Song", "Artist", 200, url=YT)]

    monkeypatch.setattr(fetch, "youtube_block_left", lambda: 60.0)
    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "resolve", one_version)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 200)
    await fetch.fetch_audio("lazy://Artist/Song", meta)
    assert asked == [("title://", False), (YT, True)]
