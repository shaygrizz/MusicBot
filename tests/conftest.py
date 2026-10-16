import pytest

from musicbot.download import SearchResult
from musicbot.search.models import Canon


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKEN")


@pytest.fixture(autouse=True)
def _isolated_audio_cache(monkeypatch, tmp_path):
    """Каждый тест со своим пустым кэшем звука: тесты не видят файлов друг друга и не пишут в data/."""
    from musicbot import audiocache

    monkeypatch.setattr(audiocache, "CACHE_DIR", tmp_path / "audio-cache")


def make_result(title, artist="", duration=0, source="ytm", url=None):
    return SearchResult(
        id="",
        title=title,
        artist=artist,
        duration=duration,
        url=url or f"https://example.com/{title}/{artist}/{duration}",
        source=source,
    )


@pytest.fixture
def result():
    return make_result


@pytest.fixture
def weeknd():
    return Canon("The Weeknd", "Blinding Lights", 200)
