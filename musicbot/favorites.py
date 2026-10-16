"""Отправка песен в чат Telegram: избранное и плейлисты. Общий код для бота и мини-аппа."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from . import db, download, favcache, search, texts

SEND_TRIES = 4  # столько раз ждём, если Telegram просит притормозить (много песен подряд из кэша)


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


def source_key(item: dict) -> str:
    """По какой ссылке песня качается и под каким ключом её file_id лежит в кэше Telegram."""
    return item.get("url") or search.lazy_for(item["artist"], item["title"], item["duration"])


async def _cached(item: dict) -> tuple | None:
    """file_id уже отправленной песни. Пересланная из другого бота песня лежит под «ленивым» ключом."""
    keys = dict.fromkeys([source_key(item), search.lazy_for(item["artist"], item["title"], item["duration"])])
    for key in keys:
        cached = await db.get_cached(f"audio:{key}")
        if cached and item["duration"] and cached[3] and abs(cached[3] - item["duration"]) > 15:
            continue  # в кэше другая версия (не та длительность)
        if cached:
            return cached
    return None


async def prepare_favorite(user_id: int, fav: dict) -> dict:
    """Подготавливает песню к отправке. Порядок: кэш Telegram (мгновенно), файл на диске, и только потом загрузка."""
    cached = await _cached(fav)
    d = None
    if not cached:
        _, path = await favcache.ensure(
            user_id, fav["artist"], fav["title"], fav["duration"], fav["cover"], fav.get("url") or ""
        )
        d = CachedFile(path, fav)
    return {"lazy": source_key(fav), "cover": fav["cover"], "cached": cached, "d": d}


async def prepare_track(user_id: int, item: dict) -> dict:
    """Песня из плейлиста: кэш Telegram, иначе обычная загрузка (общий дисковый кэш, подбор версии, проверка длины)."""
    cached = await _cached(item)
    d = None
    if not cached:
        key = source_key(item)
        meta = download.SearchResult(
            id="", title=item["title"], artist=item["artist"], duration=item["duration"], url=key,
            thumb_url=item.get("cover") or "",
        )  # fmt: skip
        d = await search.fetch_audio(key, meta)
    return {"lazy": source_key(item), "cover": item.get("cover") or "", "cached": cached, "d": d}


async def _send_audio(bot: Bot, chat_id: int, audio, **kwargs):
    """Telegram ограничивает частоту сообщений: на «подождите N секунд» ждём и повторяем ту же песню."""
    for attempt in range(SEND_TRIES):
        try:
            return await bot.send_audio(chat_id, audio, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == SEND_TRIES - 1:
                raise
            logging.info("Telegram просит подождать %s с перед следующей песней", e.retry_after)
            await asyncio.sleep(e.retry_after + 0.5)


async def deliver_favorite(bot: Bot, chat_id: int, user_id: int, prep: dict) -> None:
    cached, d = prep["cached"], prep["d"]
    try:
        if cached:
            file_id, title, artist, duration = cached
            play_id = await db.log_play(user_id, title, artist, duration, "cache", prep["lazy"], "audio", prep["cover"])
            await _send_audio(
                bot,
                chat_id,
                file_id,
                title=title,
                performer=artist,
                duration=duration,
                reply_markup=fav_keyboard(play_id, await db.fav_has(user_id, artist, title)),
            )
        else:
            play_id = await db.log_play(
                user_id, d.title, d.artist, d.duration, d.source, prep["lazy"], "audio", prep["cover"]
            )
            sent = await _send_audio(
                bot,
                chat_id,
                FSInputFile(d.path),
                title=d.title,
                performer=d.artist,
                duration=d.duration,
                thumbnail=FSInputFile(d.thumb) if d.thumb else None,
                reply_markup=fav_keyboard(play_id, await db.fav_has(user_id, d.artist, d.title)),
            )
            await db.set_cached(f"audio:{prep['lazy']}", sent.audio.file_id, d.title, d.artist, d.duration)
    finally:
        if d:
            d.cleanup()


async def send_tracks(
    bot: Bot,
    chat_id: int,
    user_id: int,
    batch: list[dict],
    prepare: Callable[[int, dict], Awaitable[dict]],
    progress: bool = True,
    stopped: Callable[[], bool] = lambda: False,
    status_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Отправляет песни одну за другой. Telegram проигрывает аудио в чате снизу вверх (после песни идёт сообщение
    выше), поэтому отправляем в обратном порядке: первая песня списка оказывается внизу и играет первой.
    Пока уходит текущая, следующая уже готовится. stopped() проверяется между песнями;
    status_markup — кнопки под сообщением о ходе отправки (например, «Стоп»).
    Возвращает, сколько песен не удалось отправить."""
    batch = list(reversed(batch))
    status = (
        await bot.send_message(chat_id, texts.say("fav_progress", i=1, n=len(batch)), reply_markup=status_markup)
        if progress
        else None
    )
    failed = 0
    nxt = asyncio.create_task(prepare(user_id, batch[0]))
    try:
        for i in range(len(batch)):
            try:
                prep = await nxt
            except Exception:
                logging.exception("песня не подготовлена")
                prep = None
                failed += 1
            nxt = None
            if stopped():  # нажали «Стоп»: готовую песню не отправляем, остальные не готовим
                if prep and prep["d"]:
                    prep["d"].cleanup()
                break
            nxt = asyncio.create_task(prepare(user_id, batch[i + 1])) if i + 1 < len(batch) else None
            if prep is not None:
                try:
                    await deliver_favorite(bot, chat_id, user_id, prep)
                except Exception:
                    logging.exception("песня не отправлена")
                    failed += 1
            if nxt is not None and status is not None:
                try:
                    await status.edit_text(texts.say("fav_progress", i=i + 2, n=len(batch)), reply_markup=status_markup)
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


async def play_favorites(bot: Bot, chat_id: int, user_id: int, batch: list[dict], progress: bool = True) -> int:
    return await send_tracks(bot, chat_id, user_id, batch, prepare_favorite, progress)
