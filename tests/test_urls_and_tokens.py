import pytest

from musicbot import favcache
from musicbot.download.urls import extract_url, unwrap_url


def test_extract_url_strips_trailing_punctuation():
    assert extract_url("look: https://www.youtube.com/watch?v=abc.") == "https://www.youtube.com/watch?v=abc"


def test_extract_url_returns_none_without_a_link():
    assert extract_url("just words") is None


def test_tiktok_login_wrapper_is_unwrapped():
    wrapped = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2F%40a%2Fvideo%2F123"
    assert unwrap_url(wrapped) == "https://www.tiktok.com/@a/video/123"


def test_tiktok_tracking_parameters_are_removed():
    assert unwrap_url("https://www.tiktok.com/@a/video/123?_r=1&_t=x") == "https://www.tiktok.com/@a/video/123"


def test_file_token_is_stable_and_bound_to_user_and_track():
    a = favcache.token_for(1, "Queen", "Bohemian Rhapsody")
    assert a == favcache.token_for(1, "Queen", "Bohemian Rhapsody")
    assert a != favcache.token_for(2, "Queen", "Bohemian Rhapsody")
    assert a != favcache.token_for(1, "Queen", "Radio Ga Ga")
    assert len(a) == 32


def test_file_token_depends_on_the_bot_token(monkeypatch):
    a = favcache.token_for(1, "Queen", "Bohemian Rhapsody")
    monkeypatch.setenv("BOT_TOKEN", "777:OTHER")
    assert a != favcache.token_for(1, "Queen", "Bohemian Rhapsody")


@pytest.mark.parametrize("bad", ["../secret", "abc", "g" * 32, "A" * 32, ""])
def test_malformed_tokens_never_reach_the_disk(bad):
    assert favcache.find(bad) is None
