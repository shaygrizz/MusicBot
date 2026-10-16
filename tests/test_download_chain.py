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
