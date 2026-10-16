import time

import pytest

from musicbot.download import chain

YT = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(chain, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(chain, "_yt_blocked_until", 0.0)


def failing(message, calls, name):
    def run(workdir):
        calls.append(name)
        raise RuntimeError(message)

    return run


def use_strategies(monkeypatch, items):
    monkeypatch.setattr(chain, "_strategies", lambda url, kind, meta, fallbacks=True: iter(items))


def test_single_403_does_not_disable_youtube(monkeypatch):
    calls = []
    use_strategies(
        monkeypatch,
        [
            ("parallel", failing("HTTP Error 403: Forbidden", calls, "parallel"), True),
            ("yt-dlp", failing("HTTP Error 403: Forbidden", calls, "yt-dlp"), True),
            ("soundcloud", failing("no match", calls, "soundcloud"), False),
        ],
    )
    with pytest.raises(RuntimeError):
        chain._download_sync(YT, "audio")
    assert calls == ["parallel", "yt-dlp", "soundcloud"]  # остальные клиенты YouTube всё равно пробуются
    assert chain._yt_blocked_until == 0.0  # и YouTube для других песен не отключается


def test_not_a_bot_blocks_youtube_for_a_short_time(monkeypatch):
    calls = []
    use_strategies(
        monkeypatch,
        [
            ("parallel", failing("Sign in to confirm you're not a bot", calls, "parallel"), True),
            ("yt-dlp", failing("never reached", calls, "yt-dlp"), True),
            ("soundcloud", failing("no match", calls, "soundcloud"), False),
        ],
    )
    with pytest.raises(RuntimeError):
        chain._download_sync(YT, "audio")
    assert calls == ["parallel", "soundcloud"]  # те же блокировки получат и другие клиенты: сразу дальше
    remaining = chain._yt_blocked_until - time.time()
    assert 0 < remaining <= chain.YT_BLOCK_TTL


def test_unavailable_video_skips_only_this_video(monkeypatch):
    calls = []
    use_strategies(
        monkeypatch,
        [
            ("parallel", failing("Video unavailable", calls, "parallel"), True),
            ("yt-dlp", failing("never reached", calls, "yt-dlp"), True),
        ],
    )
    with pytest.raises(RuntimeError):
        chain._download_sync(YT, "audio")
    assert calls == ["parallel"]
    assert chain._yt_blocked_until == 0.0


def test_skipped_youtube_is_named_in_the_error(monkeypatch):
    monkeypatch.setattr(chain, "_yt_blocked_until", time.time() + 60)
    use_strategies(monkeypatch, [])
    with pytest.raises(RuntimeError, match="YouTube временно пропущен"):
        chain._download_sync(YT, "audio")


def test_blocked_youtube_goes_straight_to_fallbacks(monkeypatch, result):
    monkeypatch.setattr(chain, "_yt_blocked_until", time.time() + 60)
    names = [name for name, _, _ in chain._strategies(YT, "audio", result("Song", "Artist", 200))]
    assert "parallel" not in names
    assert names[0] == "soundcloud"


def test_youtube_chain_order_when_not_blocked(result):
    names = [name for name, _, _ in chain._strategies(YT, "audio", result("Song", "Artist", 200))]
    assert names[:2] == ["parallel", "yt-dlp"]
    assert names[-1] == "dydki"


def test_too_big_file_stops_the_chain(monkeypatch):
    calls = []
    use_strategies(
        monkeypatch,
        [
            ("parallel", failing(chain.TOO_BIG, calls, "parallel"), True),
            ("yt-dlp", failing("never reached", calls, "yt-dlp"), True),
        ],
    )
    with pytest.raises(RuntimeError, match=chain.TOO_BIG):
        chain._download_sync(YT, "audio")
    assert calls == ["parallel"]


def test_fallback_sources_reach_their_providers(monkeypatch, tmp_path):
    """После переноса в пакет запасные источники импортировали модули по старым именам («No module named 'hitmo'»)."""
    from musicbot.download import fallbacks
    from musicbot.providers import audius, hitmo

    monkeypatch.setattr(hitmo, "download", lambda url, dest: dest.write_bytes(b"mp3"))
    monkeypatch.setattr(audius, "download", lambda track_id, dest: dest.write_bytes(b"mp3"))
    path, info = fallbacks.hitmo_download("hitmo://https%3A%2F%2Fx%2Fa.mp3", "audio", tmp_path)
    assert path.read_bytes() == b"mp3" and info["extractor_key"] == "hitmo"
    path, info = fallbacks.audius_download("audius://abc", "audio", tmp_path)
    assert path.read_bytes() == b"mp3" and info["extractor_key"] == "Audius"


def test_every_youtube_request_gets_cookies_js_runtime_and_ipv4(monkeypatch, tmp_path):
    """Куки и JS-движок раньше получал только запасной способ скачивания, а основной путь шёл без них."""
    from musicbot.download import youtube

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File")
    monkeypatch.setattr(youtube, "COOKIES_FILE", cookies)
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "node")
    monkeypatch.setattr(youtube, "FORCE_IPV4", True)
    opts = youtube.youtube_opts({"quiet": True})
    assert opts == {
        "quiet": True,
        "cookiefile": str(cookies),
        "js_runtimes": {"node": {}},
        "source_address": "0.0.0.0",
    }
    monkeypatch.setattr(youtube, "COOKIES_FILE", tmp_path / "missing.txt")
    monkeypatch.delenv("YTDLP_JS_RUNTIME")
    monkeypatch.setattr(youtube, "FORCE_IPV4", False)
    assert youtube.youtube_opts({"quiet": True}) == {"quiet": True}
