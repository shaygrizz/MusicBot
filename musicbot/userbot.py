"""Юзербот-агент для файлов больше 50 МБ.

Бот по правилам Telegram не может загрузить файл крупнее 50 МБ, а обычный аккаунт может (до 2 ГБ).
Поэтому отдельный аккаунт («агент») по MTProto (Telethon) заливает файл в личный чат с ботом,
бот получает от Telegram свой file_id и отправляет по нему песню пользователю. Тот же file_id
кладётся в кэш, и в следующий раз песня уходит мгновенно.

Включается тремя переменными: USERBOT_API_ID, USERBOT_API_HASH, USERBOT_SESSION (строка сессии,
её выдаёт `python -m musicbot.userbot_login`). Без них модуль ничего не делает, бот работает как раньше."""

import asyncio
import logging
import os
import secrets
import subprocess
from pathlib import Path

from aiogram import Bot, Router
from aiogram.types import Message

from . import download

router = Router()  # подключается в bot.py раньше остальных: файл агента не должен попасть в распознавание музыки

API_ID = int(os.getenv("USERBOT_API_ID", "").strip() or 0)
API_HASH = os.getenv("USERBOT_API_HASH", "").strip()
SESSION = os.getenv("USERBOT_SESSION", "").strip()
# самый крупный файл, который отдаём; диск на хостинге общий с базой, поэтому по умолчанию не 2 ГБ
MAX_MB = int(os.getenv("USERBOT_MAX_MB", "1000"))
PARALLEL_UPLOADS = 2

_client = None
_agent_id = 0
_bot_username = ""
_waiting: dict[str, asyncio.Future] = {}  # метка в подписи файла -> ожидание file_id
_slots = asyncio.Semaphore(PARALLEL_UPLOADS)


def configured() -> bool:
    return bool(API_ID and API_HASH and SESSION)


def ready() -> bool:
    return _client is not None


def _from_agent(m: Message) -> bool:
    """Только наши загрузки: файл от агента с меткой, которую кто-то ждёт. Остальное идёт как обычно."""
    return bool(m.from_user and m.from_user.id == _agent_id and (m.caption or "") in _waiting)


@router.message(_from_agent)
async def on_agent_upload(m: Message):
    waiter = _waiting.pop(m.caption or "", None)
    media = m.audio or m.video or m.document
    if waiter is None or waiter.done():
        return
    if media is None:
        waiter.set_exception(RuntimeError("агент прислал не файл"))
    else:
        waiter.set_result(media.file_id)


async def start(bot: Bot) -> bool:
    """Подключает агента. Любая неудача (нет Telethon, сессия устарела) только выключает функцию."""
    global _client, _agent_id, _bot_username
    if not configured():
        return False
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
        await client.connect()
        if not await client.is_authorized():
            await client.disconnect()
            logging.error("юзербот: сессия недействительна, создайте новую (python -m musicbot.userbot_login)")
            return False
        me = await client.get_me()
        _bot_username = (await bot.get_me()).username
        if not await client.get_messages(_bot_username, limit=1):  # чата с ботом ещё нет — открываем его
            await client.send_message(_bot_username, "/start")
    except Exception:
        logging.exception("юзербот не запустился, файлы больше 50 МБ отправляться не будут")
        return False
    _client, _agent_id = client, me.id
    download.set_size_limit(MAX_MB * 1024 * 1024)
    logging.info("юзербот запущен (id %s), файлы до %s МБ", me.id, MAX_MB)
    return True


async def stop() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.disconnect()


def _video_size(path: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:
        return 0, 0


def _attributes(kind: str, path: Path, title: str, artist: str, duration: int) -> list:
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

    if kind == "audio":
        return [DocumentAttributeAudio(duration=duration, title=title, performer=artist)]
    w, h = _video_size(path)
    return [DocumentAttributeVideo(duration=duration, w=w, h=h, supports_streaming=True)]


async def upload(kind: str, path: Path, title: str, artist: str, duration: int, thumb: Path | None = None) -> str:
    """Заливает файл от имени агента в чат с ботом и возвращает file_id для send_audio / send_video."""
    if _client is None:
        raise RuntimeError("юзербот не запущен")
    label = secrets.token_hex(8)
    waiter: asyncio.Future = asyncio.get_running_loop().create_future()
    _waiting[label] = waiter
    try:
        async with _slots:
            await _client.send_file(
                _bot_username,
                path,
                caption=label,
                attributes=_attributes(kind, path, title, artist, duration),
                thumb=thumb,
                force_document=False,
                supports_streaming=kind == "video",
            )
            # сообщение уже в чате; бот получит его через getUpdates за секунды
            return await asyncio.wait_for(waiter, timeout=120)
    finally:
        _waiting.pop(label, None)
