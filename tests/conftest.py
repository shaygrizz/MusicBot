import pytest

from musicbot.download import SearchResult
from musicbot.search.models import Canon


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKEN")


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
