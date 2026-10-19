"""Бот: плейлисты по ссылке (YouTube, YouTube Music, Spotify) и песни, пересланные из других ботов и чатов."""

import asyncio
import html
import logging
import os
import secrets
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import db, download, favcache, favorites, paths, playlists, search, texts
from .access import allowed

router = Router()

BATCH = int(os.getenv("PLAYLIST_BATCH", "10"))  # столько песен за одно нажатие, как «Play all» в избранном
SHOWN = 10  # столько названий показываем в сообщении
MISSED_SHOWN = 15  # столько ненайденных песен перечисляем в итоге
READ_TIMEOUT = 45
STORE_TTL = 3600
INBOX_QUIET = 2.0  # пересланные песни приходят по одной: ждём паузу и показываем одно общее сообщение
TG_DOWNLOAD_MAX = 20 * 1024 * 1024  # больше Telegram не даёт боту скачать

# одновременно по всем пользователям: процессор сервера слабый, а YouTube режет частые загрузки
_slots: asyncio.Semaphore | None = None
JOBS = int(os.getenv("PLAYLIST_JOBS", "2"))

stored: dict[str, tuple[int, float, playlists.Playlist]] = {}  # токен -> (пользователь, время, плейлист)
links: dict[str, str] = {}  # токен кнопки «Весь плейлист» -> ссылка на плейлист
jobs: dict[int, dict] = {}  # пользователь -> {"stop": bool, "task": Task}
inbox: dict[int, dict] = {}  # пользователь -> пересланные песни, ждущие решения
_background: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def _purge() -> None:
    now = time.time()
    for k in [k for k, (_, ts, _) in stored.items() if now - ts > STORE_TTL]:
        stored.pop(k, None)
    while len(links) > 2000:
        links.pop(next(iter(links)))


async def playlist_link(m: Message) -> dict | bool:
    """Фильтр: в тексте ссылка на плейлист или альбом. Найденное передаётся обработчику как found."""
    url = download.extract_url(m.text or "")
    found = playlists.classify(url) if url else None
    return {"found": found} if found else False


def whole_playlist_button(url: str) -> InlineKeyboardButton | None:
    """Под ссылкой на песню из плейлиста (watch?v=…&list=…): кнопка, чтобы взять весь плейлист."""
    lid = playlists.playlist_of_single(url)
    if not lid:
        return None
    key = secrets.token_urlsafe(6)
    links[key] = f"https://www.youtube.com/playlist?list={lid}"
    return InlineKeyboardButton(text=texts.say("pl_whole"), callback_data=f"plr:{key}")


def _summary(pl: playlists.Playlist) -> str:
    lines = [texts.say("pl_title", title=html.escape(pl.title), n=len(pl.tracks))]
    if pl.cut:
        lines.append(texts.say("pl_cut", n=len(pl.tracks)))
    lines.append("")
    for i, t in enumerate(pl.tracks[:SHOWN], 1):
        who, name = html.escape(t.artist), html.escape(t.title)
        lines.append(f"{i}. {who} - {name}" if who else f"{i}. {name}")
    if len(pl.tracks) > SHOWN:
        lines.append("\n" + texts.say("pl_rest", n=len(pl.tracks) - SHOWN))
    return "\n".join(lines)


def _keyboard(tok: str, pl: playlists.Playlist) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.say("pl_send", n=min(BATCH, len(pl.tracks))), callback_data=f"pl:s:{tok}:0"
                )
            ],
            [InlineKeyboardButton(text=texts.say("pl_fav", n=len(pl.tracks)), callback_data=f"pl:f:{tok}")],
            [InlineKeyboardButton(text=texts.say("pl_cancel"), callback_data=f"pl:x:{tok}")],
        ]
    )


async def show_playlist(m: Message, kind: str, ident: str) -> None:
    _purge()
    status = await m.answer(texts.say("pl_reading"))
    try:
        pl = await asyncio.wait_for(asyncio.to_thread(playlists.read, kind, ident), READ_TIMEOUT)
    except Exception as e:
        logging.warning("плейлист не прочитан (%s %s): %s", kind, ident, str(e)[:200])
        await status.edit_text(texts.say("pl_read_fail"))
        return
    if not pl.tracks:
        await status.edit_text(texts.say("pl_empty"))
        return
    logging.info("плейлист %r (%s): %d песен%s", pl.title, pl.source, len(pl.tracks), ", обрезан" if pl.cut else "")
    tok = secrets.token_urlsafe(6)
    stored[tok] = (m.chat.id, time.time(), pl)
    await status.edit_text(_summary(pl), parse_mode=ParseMode.HTML, reply_markup=_keyboard(tok, pl))


@router.message(F.text, playlist_link)
async def on_playlist_link(m: Message, found: tuple[str, str]):
    if not allowed(m.from_user.id):
        return
    await db.touch_user(m.from_user.id, m.from_user.full_name)
    await show_playlist(m, *found)


@router.callback_query(F.data.startswith("plr:"))
async def on_whole_playlist(cb: CallbackQuery):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    url = links.get(cb.data.split(":", 1)[1])
    found = playlists.classify(url) if url else None
    if not found:
        await cb.answer(texts.say("expired"), show_alert=True)
        return
    await cb.answer()
    await show_playlist(cb.message, *found)


async def _send_job(bot: Bot, chat_id: int, uid: int, tok: str, pl: playlists.Playlist, offset: int) -> None:
    global _slots
    _slots = _slots or asyncio.Semaphore(JOBS)
    job = jobs[uid]
    batch = [t.as_item() for t in pl.tracks[offset : offset + BATCH]]
    stop_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.say("pl_stop"), callback_data=f"pl:stop:{tok}")]]
    )
    queued = None
    try:
        if _slots.locked():
            queued = await bot.send_message(chat_id, texts.say("pl_queued"), reply_markup=stop_kb)
        async with _slots:
            if queued:
                await queued.delete()
                queued = None
            if job["stop"]:
                return
            missed: list[dict] = []
            failed = await favorites.send_tracks(
                bot,
                chat_id,
                uid,
                batch,
                favorites.prepare_track,
                stopped=lambda: job["stop"],
                status_markup=stop_kb,
                failures=missed,
            )
        nxt = offset + BATCH
        rows = []
        if nxt < len(pl.tracks) and not job["stop"] and tok in stored:
            n = min(BATCH, len(pl.tracks) - nxt)
            rows.append([InlineKeyboardButton(text=texts.say("pl_next", n=n), callback_data=f"pl:s:{tok}:{nxt}")])
        text = texts.say("pl_stopped") if job["stop"] else texts.say("pl_done", sent=len(batch) - failed, n=len(batch))
        if failed and not job["stop"]:
            text += "\n" + texts.say("pl_failed", n=failed)
            # какие именно: их можно найти вручную или переслать из другого бота
            names = [f"• {html.escape(m['artist'])} - {html.escape(m['title'])}" for m in missed[:MISSED_SHOWN]]
            if len(missed) > MISSED_SHOWN:
                names.append(texts.say("pl_rest", n=len(missed) - MISSED_SHOWN))
            text += "\n" + "\n".join(names)
        if nxt < len(pl.tracks) and not job["stop"]:
            text += "\n" + texts.say("pl_left", n=len(pl.tracks) - nxt)
        await bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
        )
    except Exception:
        logging.exception("плейлист не отправлен")
        await bot.send_message(chat_id, texts.say("pl_send_fail"))
    finally:
        if queued:
            try:
                await queued.delete()
            except Exception:
                pass
        jobs.pop(uid, None)


@router.callback_query(F.data.startswith("pl:"))
async def on_playlist_button(cb: CallbackQuery, bot: Bot):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    uid = cb.from_user.id
    parts = cb.data.split(":")
    action, tok = parts[1], parts[2]
    if action == "stop":
        job = jobs.get(uid)
        if job:
            job["stop"] = True
        await cb.answer(texts.say("pl_stopping"))
        return
    entry = stored.get(tok)
    if not entry:
        await cb.answer(texts.say("expired"), show_alert=True)
        return
    pl = entry[2]
    if action == "x":
        stored.pop(tok, None)
        await cb.answer()
        try:
            await cb.message.delete()
        except Exception:
            pass
        return
    if action == "f":
        added = await db.fav_add_many(uid, [t.as_item() for t in pl.tracks], "bot")
        await cb.answer(texts.say("pl_fav_done", n=added, total=len(pl.tracks)), show_alert=True)
        favcache.spawn(favcache.backfill(uid, await db.fav_list(uid)))  # песни качаются в фоне по одной
        return
    if action == "s":
        if uid in jobs:
            await cb.answer(texts.say("pl_busy"), show_alert=True)
            return
        try:
            offset = max(0, int(parts[3]))
        except (IndexError, ValueError):
            offset = 0
        await cb.answer()
        jobs[uid] = {"stop": False}
        jobs[uid]["task"] = _spawn(_send_job(bot, cb.message.chat.id, uid, tok, pl, offset))
        return
    await cb.answer()


# --- песни, пересланные из других ботов и чатов ---


def audio_name(audio) -> tuple[str, str] | None:
    """Исполнитель и название из тегов файла, иначе из имени файла «Исполнитель - Название.mp3»."""
    artist, title = (audio.performer or "").strip(), (audio.title or "").strip()
    if title:
        return artist, title
    stem = Path(audio.file_name or "").stem.replace("_", " ").strip()
    if " - " in stem:
        left, right = (x.strip() for x in stem.split(" - ", 1))
        if left and right:
            return left, right
    return None


def _suffix(audio) -> str | None:
    mime = (audio.mime_type or "").lower()
    if mime == "audio/mpeg":
        return ".mp3"
    if mime in ("audio/mp4", "audio/x-m4a", "audio/m4a"):
        return ".m4a"
    ext = Path(audio.file_name or "").suffix.lower()
    return ext if ext in (".mp3", ".m4a") else None


def _inbox_text(box: dict) -> str:
    items = list(box["items"].values())
    lines = [texts.say("in_title", n=len(items)), ""]
    for i, it in enumerate(items[:SHOWN], 1):
        who, name = html.escape(it["artist"]), html.escape(it["title"])
        lines.append(f"{i}. {who} - {name}" if who else f"{i}. {name}")
    if len(items) > SHOWN:
        lines.append("\n" + texts.say("pl_rest", n=len(items) - SHOWN))
    return "\n".join(lines)


def _inbox_keyboard(n: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.say("in_fav", n=n), callback_data="in:f")],
            [InlineKeyboardButton(text=texts.say("pl_cancel"), callback_data="in:x")],
        ]
    )


async def _show_inbox(bot: Bot, uid: int) -> None:
    await asyncio.sleep(INBOX_QUIET)
    box = inbox.get(uid)
    if not box or not box["items"]:
        return
    text, kb = _inbox_text(box), _inbox_keyboard(len(box["items"]))
    if box.get("msg"):
        try:
            await box["msg"].edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            pass  # сообщение удалили или текст не изменился — присылаем новое
    box["msg"] = await bot.send_message(box["chat"], text, parse_mode=ParseMode.HTML, reply_markup=kb)


def collect(bot: Bot, m: Message, name: tuple[str, str]) -> None:
    """Копит пересланные песни; после паузы показывает одно сообщение со списком (а не ответ на каждую)."""
    uid, a = m.from_user.id, m.audio
    box = inbox.setdefault(uid, {"items": {}, "chat": m.chat.id, "msg": None, "task": None})
    box["chat"] = m.chat.id
    box["items"][a.file_unique_id] = {
        "artist": name[0],
        "title": name[1],
        "duration": int(a.duration or 0),
        "cover": "",
        "url": "",
        "file_id": a.file_id,
        "size": a.file_size or 0,
        "suffix": _suffix(a),
    }
    if box["task"] and not box["task"].done():
        box["task"].cancel()
    box["task"] = _spawn(_show_inbox(bot, uid))


async def named_forward(m: Message) -> dict | bool:
    """Фильтр: пересланная (или присланная через другого бота) песня с названием. Без названия файл уходит
    в обычное распознавание по звуку."""
    if not m.audio or not (m.forward_origin or m.via_bot):
        return False
    name = audio_name(m.audio)
    return {"name": name} if name else False


@router.message(named_forward)
async def on_forwarded_audio(m: Message, bot: Bot, name: tuple[str, str]):
    """Песня из другого бота или чата: это уже готовый файл с названием, распознавать её не нужно."""
    if not allowed(m.from_user.id):
        return
    await db.touch_user(m.from_user.id, m.from_user.full_name)
    collect(bot, m, name)


async def _pull_files(bot: Bot, uid: int, items: list[dict]) -> None:
    """Файлы пересланных песен — в кэш избранного, чтобы мини-апп играл их с диска, не ища на YouTube."""
    for it in items:
        if not it["suffix"] or (it["size"] and it["size"] > TG_DOWNLOAD_MAX):
            continue  # такие сыграет кэш Telegram в чате; мини-апп при необходимости найдёт песню сам
        if favcache.has(uid, it["artist"], it["title"]):
            continue
        dest = paths.DOWNLOAD_DIR / f"fwd-{secrets.token_hex(5)}{it['suffix']}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await bot.download(it["file_id"], destination=dest)
            await favcache.adopt(uid, it["artist"], it["title"], dest)
        except Exception as e:
            logging.info("пересланная песня не сохранена %s - %s: %s", it["artist"], it["title"], str(e)[:120])
        finally:
            dest.unlink(missing_ok=True)


@router.callback_query(F.data.startswith("in:"))
async def on_inbox_button(cb: CallbackQuery, bot: Bot):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    uid = cb.from_user.id
    box = inbox.pop(uid, None)
    if box and box["task"] and not box["task"].done():
        box["task"].cancel()
    if cb.data == "in:x" or not box or not box["items"]:
        await cb.answer()
        try:
            await cb.message.delete()
        except Exception:
            pass
        return
    items = list(box["items"].values())
    added = await db.fav_add_many(uid, items, "bot")
    for it in items:
        # тот же ключ, что посчитает избранное при отправке в чат: песня уйдёт по file_id мгновенно, без YouTube
        key = search.lazy_for(it["artist"], it["title"], it["duration"])
        await db.set_cached(f"audio:{key}", it["file_id"], it["title"], it["artist"], it["duration"])
    await cb.answer()
    try:
        await cb.message.edit_text(texts.say("in_done", n=added, total=len(items)))
    except Exception:
        pass
    favcache.spawn(_pull_files(bot, uid, items))
