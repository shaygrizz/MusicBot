import time

import pytest

from musicbot.download import chain, youtube

YT = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(chain, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(youtube, "_blocked_until", 0.0)
    monkeypatch.setattr(youtube, "YT_PROXY", "")


def failing(message, calls, name):
    def run(workdir):
        calls.append(name)
        raise RuntimeError(message)

    return run


def use_strategies(monkeypatch, items):
    monkeypatch.setattr(chain, "_strategies", lambda url, kind, meta, fallbacks=True, ignore_block=False: iter(items))


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
    assert youtube.youtube_block_left() == 0  # и YouTube для других песен не отключается


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
    assert 0 < youtube.youtube_block_left() <= youtube.YT_BLOCK_TTL


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
    assert youtube.youtube_block_left() == 0


def test_skipped_youtube_is_named_in_the_error(monkeypatch):
    monkeypatch.setattr(youtube, "_blocked_until", time.time() + 60)
    use_strategies(monkeypatch, [])
    with pytest.raises(RuntimeError, match="YouTube временно пропущен"):
        chain._download_sync(YT, "audio")


def test_blocked_youtube_goes_straight_to_fallbacks(monkeypatch, result):
    monkeypatch.setattr(youtube, "_blocked_until", time.time() + 60)
    names = [name for name, _, _ in chain._strategies(YT, "audio", result("Song", "Artist", 200))]
    assert "parallel" not in names
    assert names[0] == "hitmo"


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


def test_cookies_file_in_the_wrong_format_is_skipped_not_fatal(monkeypatch, tmp_path):
    """JSON из расширения браузера yt-dlp не принимает и роняет каждое скачивание; такой файл пропускаем."""
    bad = tmp_path / "cookies.txt"
    bad.write_text('[{"domain": ".youtube.com", "name": "SID", "value": "x"}]')
    monkeypatch.setattr(youtube, "COOKIES_FILE", bad)
    monkeypatch.setattr(youtube, "_cookies_checked", None)
    assert "cookiefile" not in youtube.youtube_opts({})

    row = "\t".join([".youtube.com", "TRUE", "/", "TRUE", "0", "SID", "x"])
    good = tmp_path / "good.txt"
    good.write_text("\ufeff# Netscape HTTP Cookie File\n" + row + "\n", encoding="utf-8")
    monkeypatch.setattr(youtube, "COOKIES_FILE", good)
    assert youtube.youtube_opts({})["cookiefile"] == str(good)

    tabs_only = tmp_path / "tabs.txt"  # без заголовка, но строки в формате Netscape — yt-dlp такой примет
    tabs_only.write_text(row + "\n", encoding="utf-8")
    monkeypatch.setattr(youtube, "COOKIES_FILE", tabs_only)
    assert youtube.youtube_opts({})["cookiefile"] == str(tabs_only)


def test_age_gate_does_not_block_youtube_for_everyone(monkeypatch):
    """«Sign in to confirm your age» — ограничение одного ролика, а не блокировка IP: раньше оно отключало YouTube на 2 минуты."""
    calls = []
    use_strategies(
        monkeypatch,
        [
            (
                "parallel",
                failing("Sign in to confirm your age. This video may be inappropriate", calls, "parallel"),
                True,
            ),
            ("yt-dlp:tv", failing("never mind", calls, "yt-dlp:tv"), True),
            ("soundcloud", failing("no match", calls, "soundcloud"), False),
        ],
    )
    with pytest.raises(RuntimeError):
        chain._download_sync(YT, "audio")
    assert calls == [
        "parallel",
        "yt-dlp:tv",
        "soundcloud",
    ]  # другие клиенты пробуются: tv часто проходит возрастное ограничение
    assert youtube.youtube_block_left() == 0


def test_unavailable_format_still_tries_other_clients(monkeypatch):
    calls = []
    use_strategies(
        monkeypatch,
        [
            ("yt-dlp", failing("Requested format is not available", calls, "yt-dlp"), True),
            ("yt-dlp:tv", failing("never mind", calls, "yt-dlp:tv"), True),
        ],
    )
    with pytest.raises(RuntimeError):
        chain._download_sync(YT, "audio")
    assert calls == ["yt-dlp", "yt-dlp:tv"]


def test_wrong_length_fallback_file_leads_to_the_next_fallback(monkeypatch, result, tmp_path):
    """Отрывок на 30 секунд из SoundCloud раньше обрывал цепочку: hitmo и dydki уже не пробовались."""
    meta = result("Song", "Artist", 200)
    lengths = {"preview.mp3": 30, "full.mp3": 201}

    def writes(name):
        def run(workdir):
            path = workdir / name
            path.write_bytes(b"x" * 10)
            return path, {"id": name}

        return run

    use_strategies(monkeypatch, [("soundcloud", writes("preview.mp3"), False), ("hitmo", writes("full.mp3"), False)])
    monkeypatch.setattr(chain, "probe_duration", lambda path: lengths[path.name])
    monkeypatch.setattr(chain, "leading_silence", lambda path: 0.0)  # без ffmpeg, как на CI
    d = chain._download_sync("title://", "audio", meta)
    assert d.path.name == "full.mp3"


def test_warm_and_info_do_not_touch_a_blocked_youtube(monkeypatch):
    import asyncio

    monkeypatch.setattr(youtube, "_blocked_until", time.time() + 60)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", lambda *a, **k: pytest.fail("YouTube не должен вызываться"))
    asyncio.run(youtube.warm(YT))
    with pytest.raises(RuntimeError, match="временно пропущен"):
        youtube._yt_info(YT)


def test_not_a_bot_while_extracting_blocks_youtube_for_prefetch_too(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, *a, **k):
            raise RuntimeError("Sign in to confirm you’re not a bot")

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(RuntimeError):
        youtube._yt_info(YT)
    assert youtube.youtube_block_left() > 0


def test_proxy_goes_to_ytdlp_and_replaces_source_address(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube, "YT_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setattr(youtube, "COOKIES_FILE", tmp_path / "missing.txt")
    monkeypatch.delenv("YTDLP_JS_RUNTIME", raising=False)
    opts = youtube.youtube_opts({})
    assert opts["proxy"] == "http://127.0.0.1:3128" and "source_address" not in opts


def test_blocked_video_download_gets_one_last_attempt(monkeypatch):
    """Видео (и ссылка без названия) запасных источников не имеют: при блокировке они раньше падали, не пробуя YouTube."""
    monkeypatch.setattr(youtube, "_blocked_until", time.time() + 60)
    seen = []

    def strategies(url, kind, meta, fallbacks=True, ignore_block=False):
        seen.append(ignore_block)
        if ignore_block:
            yield "yt-dlp", lambda wd: (_ for _ in ()).throw(RuntimeError("still blocked")), True

    monkeypatch.setattr(chain, "_strategies", strategies)
    with pytest.raises(RuntimeError, match="последняя попытка"):
        chain._download_sync(YT, "video")
    assert seen == [False, True]


def test_bad_cookies_get_a_retry_without_them_before_other_clients(monkeypatch):
    monkeypatch.setattr(chain, "cookies_usable", lambda: True)
    monkeypatch.setattr(chain, "youtube_block_left", lambda: 0)
    names = [name for name, _, _ in chain._strategies(YT, "audio", None, fallbacks=False)]
    assert names[:3] == ["parallel", "yt-dlp", "yt-dlp:nocookies"]
    monkeypatch.setattr(chain, "cookies_usable", lambda: False)
    assert "yt-dlp:nocookies" not in [name for name, _, _ in chain._strategies(YT, "audio", None, fallbacks=False)]
