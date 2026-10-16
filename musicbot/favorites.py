"""Отправка избранного в чат Telegram. Общий код для бота и мини-аппа."""

import asyncio
import logging

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from . import db, favcache, search, texts


def fav_keyboard(play_id: int, is_fav: bool) -> InlineKeyboardMarkup:
    """Кнопка под присланной песней: добавить в «My Favorites» или убрать оттуда."""
    label = texts.say("fav_remove" if is_fav else "fav_add")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"fav:{play_id}")]])


class CachedFile:
    """Файл из постоянного кэша: после отправки его нельзя удалять."""

    def __init__(self, path, fav: dict):
        self.path, self.thumb = path, None
        self.title, self.artist, self.duration = fav["title"], fav["artist"], fav["duration"]
        self.source = "favorites"

    def cleanup(self) -> None:
        pass


async def prepare_favorite(user_id: int, fav: dict) -> dict:
    """Подготавливает песню к отправке. Порядок: кэш Telegram (мгновенно), файл на диске, и только потом загрузка."""
    lazy = search.lazy_for(fav["artist"], fav["title"], fav["duration"])
    cached = await db.get_cached(f"audio:{lazy}")
    if cached and fav["duration"] and cached[3] and abs(cached[3] - fav["duration"]) > 15:
        cached = None
    d = None
    if not cached:
        _, path = await favcache.ensure(user_id, fav["artist"], fav["title"], fav["duration"], fav["cover"])
        d = CachedFile(path, fav)
    return {"lazy": lazy, "cover": fav["cover"], "cached": cached, "d": d}


async def deliver_favorite(bot: Bot, chat_id: int, user_id: int, prep: dict) -> None:
    cached, d = prep["cached"], prep["d"]
    try:
        if cached:
            file_id, title, artist, duration = cached
            play_id = await db.log_play(user_id, title, artist, duration, "cache", prep["lazy"], "audio", prep["cover"])
            await bot.send_audio(
                chat_id,
                file_id,
                title=title,
                performer=artist,
                duration=duration,
                reply_markup=fav_keyboard(play_id, True),
            )
        else:
            play_id = await db.log_play(
                user_id, d.title, d.artist, d.duration, d.source, prep["lazy"], "audio", prep["cover"]
            )
            sent = await bot.send_audio(
                chat_id,
                FSInputFile(d.path),
                title=d.title,
                performer=d.artist,
                duration=d.duration,
                thumbnail=FSInputFile(d.thumb) if d.thumb else None,
                reply_markup=fav_keyboard(play_id, True),
            )
            await db.set_cached(f"audio:{prep['lazy']}", sent.audio.file_id, d.title, d.artist, d.duration)
    finally:
        if d:
            d.cleanup()


async def play_favorites(bot: Bot, chat_id: int, user_id: int, batch: list[dict], progress: bool = True) -> int:
    """Отправляет песни одну за другой. Telegram проигрывает аудио в чате снизу вверх (после песни идёт сообщение
    выше), поэтому отправляем в обратном порядке: первая песня списка оказывается внизу и играет первой.
    Пока уходит текущая, следующая уже готовится. Возвращает, сколько песен не удалось загрузить."""
    batch = list(reversed(batch))
    status = await bot.send_message(chat_id, texts.say("fav_progress", i=1, n=len(batch))) if progress else None
    failed = 0
    nxt = asyncio.create_task(prepare_favorite(user_id, batch[0]))
    try:
        for i in range(len(batch)):
            try:
                prep = await nxt
            except Exception:
                logging.exception("favorite not prepared")
                prep = None
                failed += 1
            nxt = asyncio.create_task(prepare_favorite(user_id, batch[i + 1])) if i + 1 < len(batch) else None
            if prep is not None:
                try:
                    await deliver_favorite(bot, chat_id, user_id, prep)
                except Exception:
                    logging.exception("favorite not delivered")
                    failed += 1
            if nxt is not None and status is not None:
                try:
                    await status.edit_text(texts.say("fav_progress", i=i + 2, n=len(batch)))
                except Exception:
                    pass
    finally:
        if nxt is not None and not nxt.done():
            nxt.cancel()
        if status is not None:
            try:
                await status.delete()
            except Exception:
                pass
    return failed
