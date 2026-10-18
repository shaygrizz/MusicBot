import asyncio
from types import SimpleNamespace

import pytest

from musicbot import userbot
from musicbot.download import models


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(models, "_limit", models.MAX_SIZE)
    monkeypatch.setattr(userbot, "_agent_id", 777)
    userbot._waiting.clear()
    yield
    userbot._waiting.clear()


def message(user_id, caption, **media):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id), caption=caption, audio=None, video=None, document=None, **media
    )


def test_limit_is_49_mb_until_the_agent_starts():
    assert models.size_limit() == models.MAX_SIZE
    assert models.download_cap() == models.MAX_SIZE * 4


def test_limit_grows_with_agent_and_never_shrinks_below_telegram_cap():
    models.set_size_limit(1000 * 1024 * 1024)
    assert models.size_limit() == 1000 * 1024 * 1024
    assert models.download_cap() == 2000 * 1024 * 1024
    models.set_size_limit(1)
    assert models.size_limit() == models.MAX_SIZE


def test_filter_takes_only_labelled_files_from_the_agent():
    userbot._waiting["abc"] = object()
    assert userbot._from_agent(message(777, "abc"))
    assert not userbot._from_agent(message(777, "other"))  # свой файл агента без метки — обычное сообщение
    assert not userbot._from_agent(message(1, "abc"))  # метку подделал посторонний
    assert not userbot._from_agent(message(777, None))


def test_handler_hands_file_id_to_the_waiting_upload():
    async def run():
        waiter = asyncio.get_running_loop().create_future()
        userbot._waiting["abc"] = waiter
        m = message(777, "abc")
        m.audio = SimpleNamespace(file_id="FILE")
        await userbot.on_agent_upload(m)
        return waiter.result()

    assert asyncio.run(run()) == "FILE"


def test_upload_without_agent_fails_fast(tmp_path):
    with pytest.raises(RuntimeError, match="юзербот"):
        asyncio.run(userbot.upload("audio", tmp_path / "x.mp3", "t", "a", 10))


class FakeClient:
    """Вместо Telegram: «заливает» файл и сразу доставляет боту сообщение с меткой из подписи."""

    def __init__(self):
        self.sent = None

    async def send_file(self, to, path, **kwargs):
        self.sent = (to, path, kwargs)
        m = message(777, kwargs["caption"])
        m.audio = SimpleNamespace(file_id="BOT_FILE_ID")
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(userbot.on_agent_upload(m)))


def test_upload_sends_audio_with_tags_and_returns_bot_file_id(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(userbot, "_client", client)
    monkeypatch.setattr(userbot, "_bot_username", "TestBot")
    song = tmp_path / "mix.mp3"
    song.write_bytes(b"x")
    file_id = asyncio.run(userbot.upload("audio", song, "Mix", "DJ", 3600))
    to, path, kwargs = client.sent
    attr = kwargs["attributes"][0]
    assert file_id == "BOT_FILE_ID"
    assert (to, path) == ("TestBot", song)
    assert (attr.title, attr.performer, attr.duration) == ("Mix", "DJ", 3600)
    assert kwargs["force_document"] is False
    assert not userbot._waiting  # метка убрана
