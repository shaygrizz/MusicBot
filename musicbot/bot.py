import asyncio
import base64
import html
import logging
import os
import random
import shutil
import time
import urllib.parse
import uuid

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from . import db, download, favcache, favorites, paths, playlist_bot, recognize, restore, search, texts, tunnel
from .access import ALLOWED, allowed
from .web import app as webapp

TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))


def https_url(raw: str) -> str:
    """Telegram принимает для мини-аппа только https-ссылки: адрес без схемы («bot-1.bothost.tech») дополняем."""
    url = raw.strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


webapp_url = https_url(os.getenv("WEBAPP_URL", ""))  # свой https-адрес, если есть; иначе туннель

router = Router()


class LangMiddleware(BaseMiddleware):
    """На каждое обновление выставляет язык того, кто написал: все texts.say() внутри обработчика говорят на нём.
    Кто язык ещё не выбирал, получает язык интерфейса Telegram (по умолчанию английский)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            texts.use(await db.get_lang(user.id) or texts.guess(user.language_code))
        return await handler(event, data)


# короткие токены для callback_data (лимит 64 байта) -> (url, метаданные из поиска)
pending: dict[str, tuple[str, download.SearchResult | None]] = {}
deep_queries: dict[str, str] = {}  # токен кнопки «Искать глубже» -> очищенный запрос
# предзагрузка: url -> (время старта, задача скачивания аудио)
prefetch: dict[str, tuple[float, asyncio.Task]] = {}
PREFETCH_TOP = 2
PREFETCH_TTL = 600  # секунд, дальше файл считаем ненужным


def _drop_prefetch(url: str) -> None:
    item = prefetch.pop(url, None)
    if item:
        task = item[1]
        if task.done() and not task.cancelled() and task.exception() is None:
            task.result().cleanup()
        else:
            task.add_done_callback(
                lambda t: t.result().cleanup() if not t.cancelled() and t.exception() is None else None
            )


async def warm_lazy(r: download.SearchResult) -> None:
    """Песня из каталога: заранее находим скачиваемую версию и достаём ссылку на звук.
    К моменту нажатия остаётся только сама загрузка."""
    try:
        cands = await search.resolve(r)
        if cands:
            await download.warm(cands[0].url)
    except Exception:
        pass


def start_prefetch(results: list[download.SearchResult]) -> None:
    now = time.time()
    for url in [u for u, (ts, _) in prefetch.items() if now - ts > PREFETCH_TTL]:
        _drop_prefetch(url)
    lazy = [x for x in results if x.url.startswith("lazy://")]  # песни из каталога: источник ещё не выбран
    playable = [r for r in results if not r.url.startswith("lazy://")]
    # первую песню выдачи качаем целиком заранее, даже если она из каталога: чаще всего нажимают именно её
    full = playable[:PREFETCH_TOP] if playable and (not lazy or results[0] is playable[0]) else lazy[:1] + playable[:1]
    for r in full:
        if r.url not in prefetch:
            prefetch[r.url] = (now, asyncio.create_task(search.fetch_audio(r.url, r)))
    for r in [x for x in lazy if x not in full][:3]:  # остальным из каталога находим источник и ссылку на звук
        asyncio.create_task(warm_lazy(r))
    for r in playable[PREFETCH_TOP : PREFETCH_TOP + 4]:  # остальным заранее достаём ссылку на звук (экономит 2–3 с)
        asyncio.create_task(download.warm(r.url))


def fmt_dur(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def remember(url: str, meta: download.SearchResult | None = None) -> str:
    key = uuid.uuid4().hex[:10]
    pending[key] = (url, meta)
    if len(pending) > 2000:
        pending.pop(next(iter(pending)))
    return key


def decode_start_payload(payload: str) -> str | None:
    """Мини-апп открывает бота ссылкой /start s_<base64url(запрос)> — достаём запрос."""
    if not payload.startswith("s_"):
        return None
    raw = payload[2:]
    try:
        text = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "ignore")
    except Exception:
        return None
    return text.strip()[:100] or None


@router.message(CommandStart())
async def start(m: Message, command: CommandObject):
    if not allowed(m.from_user.id):
        return
    await db.touch_user(m.from_user.id, m.from_user.full_name)
    query = decode_start_payload(command.args or "")
    if query:
        await run_search(m, query)
        return
    if await db.get_lang(m.from_user.id) is None:  # первым делом предлагаем выбрать язык
        await m.answer(texts.say("lang_choose"), reply_markup=language_keyboard("s"))
        return
    await m.answer(texts.say("start"), reply_markup=quick_keyboard())


def language_keyboard(origin: str) -> InlineKeyboardMarkup:
    """Кнопки языков с флагами. origin: «s» — выбор при /start, «g» — из настроек (там есть «Назад» и отметка выбранного)."""
    current = texts.current()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{flag} {name}" + (" ✓" if origin == "g" and code == current else ""),
                callback_data=f"lang:{code}:{origin}",
            )
        ]
        for code, (flag, name) in texts.LANGS.items()
    ]
    if origin == "g":
        rows.append([InlineKeyboardButton(text=texts.say("settings_back"), callback_data="set:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard() -> InlineKeyboardMarkup:
    flag, name = texts.LANGS[texts.current()]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.say("settings_lang", flag=flag, name=name), callback_data="set:lang")]
        ]
    )


@router.message(Command("settings", "language"))
async def settings_cmd(m: Message):
    if not allowed(m.from_user.id):
        return
    await m.answer(texts.say("settings_title"), parse_mode=ParseMode.HTML, reply_markup=settings_keyboard())


@router.callback_query(F.data.startswith("set:"))
async def on_settings(cb: CallbackQuery):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    await cb.answer()
    try:
        if cb.data == "set:lang":
            await cb.message.edit_text(texts.say("lang_choose"), reply_markup=language_keyboard("g"))
        else:
            await cb.message.edit_text(
                texts.say("settings_title"), parse_mode=ParseMode.HTML, reply_markup=settings_keyboard()
            )
    except Exception:
        pass  # сообщение устарело или текст не изменился


@router.callback_query(F.data.startswith("lang:"))
async def on_language(cb: CallbackQuery, bot: Bot):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    _, code, origin = cb.data.split(":", 2)
    if code not in texts.LANGS:
        await cb.answer()
        return
    await db.set_lang(cb.from_user.id, code)
    texts.use(code)
    await cb.answer()
    await set_menu_button(bot, cb.from_user.id)  # подпись кнопки меню тоже на новом языке
    flag, name = texts.LANGS[code]
    try:
        if origin == "s":
            await cb.message.edit_text(texts.say("lang_set", flag=flag, name=name))
        else:
            await cb.message.edit_text(
                texts.say("settings_title"), parse_mode=ParseMode.HTML, reply_markup=settings_keyboard()
            )
    except Exception:
        pass
    # кнопки под полем ввода меняются только вместе с новым сообщением
    text = texts.say("start") if origin == "s" else texts.say("lang_set", flag=flag, name=name)
    await bot.send_message(cb.message.chat.id, text, reply_markup=quick_keyboard())


@router.message(Command("app"))
async def app_cmd(m: Message):
    if not allowed(m.from_user.id):
        return
    if not webapp_url:
        await m.answer(texts.say("app_unavailable"))
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.say("app_button"), web_app=WebAppInfo(url=webapp_url)),
            ]
        ]
    )
    await m.answer(texts.say("app_text"), reply_markup=kb)


@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(texts.say("help"), reply_markup=quick_keyboard())


@router.message(Command("stats"))
async def stats(m: Message):
    s = await db.user_stats(m.from_user.id)
    if not s["total"]:
        await m.answer(texts.say("stats_empty"))
        return
    hours = s["seconds"] / 3600
    lines = [texts.say("stats_title"), texts.say("stats_tracks", n=s["total"]), texts.say("stats_time", hours=hours)]
    if s["top_artists"]:
        lines.append(texts.say("stats_top_artists"))
        lines += [f"{i}. {html.escape(a)} ({c})" for i, (a, c) in enumerate(s["top_artists"], 1)]
    if s["top_tracks"]:
        lines.append(texts.say("stats_top_tracks"))
        lines += [
            f"{i}. {html.escape(t)}, {html.escape(a or texts.say('unknown'))} ({c})"
            for i, (t, a, c) in enumerate(s["top_tracks"], 1)
        ]
    await m.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("admin"))
async def admin(m: Message):
    """Скрытая команда (нет в меню): открывает админку тому, чей ID указан в ADMIN_USERS."""
    if not webapp.ADMINS:
        await m.answer(f"Admin is not set up. Add ADMIN_USERS={m.from_user.id} to .env and restart the bot.")
        return
    if m.from_user.id not in webapp.ADMINS:
        return  # остальным молчим
    if not webapp_url:
        await m.answer("The dashboard address is not ready yet. Try again in a minute.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open admin", web_app=WebAppInfo(url=f"{webapp_url}/admin"))]]
    )
    await m.answer("Admin panel.", reply_markup=kb)


@router.message(F.document, F.caption.startswith("/restore"))
async def restore_cmd(m: Message, bot: Bot):
    """Скрытая команда администратора для переезда: файл musicbot.db или avatars.zip с подписью /restore."""
    if m.from_user.id not in webapp.ADMINS:
        return  # остальным молчим
    doc = m.document
    name = (doc.file_name or "").lower()
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await m.answer("The file is larger than 20 MB: Telegram does not let bots download it.")
        return
    if not name.endswith((".db", ".zip")):
        await m.answer("Send musicbot.db or avatars.zip with the caption /restore.")
        return
    workdir = paths.DOWNLOAD_DIR / f"restore-{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        src = workdir / ("incoming.db" if name.endswith(".db") else "incoming.zip")
        await bot.download(doc, destination=src)
        if name.endswith(".db"):
            counts, backup = await restore.replace_database(src)
            summary = ", ".join(f"{t}: {n}" for t, n in counts.items())
            kept = f"\nPrevious database saved as {backup.name}." if backup else ""
            await m.answer(f"Database restored ({summary}).\nStored at {db.DB_PATH}.{kept}")
        else:
            n = await asyncio.to_thread(restore.restore_avatars, src, webapp.AVATAR_DIR)
            await m.answer(f"Profile pictures restored: {n}.\nStored in {webapp.AVATAR_DIR}.")
    except Exception as e:
        logging.exception("restore failed")
        await m.answer(f"Restore failed: {e}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


FAV_BATCH = 10  # столько песен отправляем за одно нажатие «Play all»
FAV_SHOWN = 10  # столько песен показываем в списке (остальные доступны через «Play all»)
playing_favs: set[int] = set()  # у кого уже идёт отправка плейлиста


def quick_keyboard() -> ReplyKeyboardMarkup:
    """Постоянные кнопки под полем ввода: плейлист «любимого» и дашборд всегда под рукой."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.say("kb_favorites")), KeyboardButton(text=texts.say("kb_dashboard"))]],
        resize_keyboard=True,
        is_persistent=True,
    )


def favorites_keyboard(favs: list[dict]) -> InlineKeyboardMarkup:
    # «Play all» и «Shuffle» — каждая в своём ряду на всю ширину; названия песен выводит текст сообщения
    # (favorites_text), а под ним компактные кнопки с номерами: в подпись кнопки длинное название не помещается.
    # Кнопки воспроизведения открывают плеер дашборда: он сам запускает очередь (бот не умеет «включить музыку»
    # в приложении Telegram). Без адреса дашборда остаётся отправка файлов в чат.
    def play_button(text: str, query: str, fallback: str) -> InlineKeyboardButton:
        if webapp_url:
            return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{webapp_url}/?{query}"))
        return InlineKeyboardButton(text=text, callback_data=fallback)

    rows = [
        [play_button(texts.say("fav_play_all"), "play=favorites", "favplay:all:0")],
        [play_button(texts.say("fav_shuffle"), "play=shuffle", "favplay:shuf:0")],
    ]
    numbers = [
        play_button(f"▶ {i}", f"play=favorites&i={i}", f"favplay:one:{f['id']}")
        for i, f in enumerate(favs[:FAV_SHOWN], 1)
    ]
    rows += [numbers[k : k + 5] for k in range(0, len(numbers), 5)]
    rows.append([InlineKeyboardButton(text=texts.say("fav_send"), callback_data="favplay:all:0")])  # файлы в чат
    return InlineKeyboardMarkup(inline_keyboard=rows)


def favorites_text(favs: list[dict]) -> str:
    """Список песен текстом: названия видны целиком, длинные переносятся на новую строку."""
    lines = [texts.say("fav_title", n=len(favs)), ""]
    for i, f in enumerate(favs[:FAV_SHOWN], 1):
        name = html.escape(f["title"])
        who = html.escape(f["artist"])
        lines.append(f"{i}. {who} - {name}" if who else f"{i}. {name}")
    if len(favs) > FAV_SHOWN:
        lines.append("\n" + texts.say("fav_rest", n=len(favs) - FAV_SHOWN))
    return "\n".join(lines)


@router.message(Command("favorites"))
@router.message(F.text.in_(texts.variants("kb_favorites")))
async def favorites_cmd(m: Message):
    if not allowed(m.from_user.id):
        return
    favs = await db.fav_list(m.from_user.id)
    if not favs:
        await m.answer(texts.say("fav_empty"))
        return
    await m.answer(favorites_text(favs), parse_mode=ParseMode.HTML, reply_markup=favorites_keyboard(favs))


@router.message(F.text.in_(texts.variants("kb_dashboard")))
async def dashboard_button(m: Message):
    await app_cmd(m)


@router.callback_query(F.data.startswith("favplay:"))
async def on_favplay(cb: CallbackQuery, bot: Bot):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    uid = cb.from_user.id
    _, mode, arg = cb.data.split(":", 2)
    favs = await db.fav_list(uid)
    if not favs:
        await cb.answer(texts.say("fav_empty"), show_alert=True)
        return
    next_offset = None
    if mode == "one":
        batch = [f for f in favs if str(f["id"]) == arg][:1]
    elif mode == "shuf":
        batch = random.sample(favs, min(FAV_BATCH, len(favs)))
    else:
        try:
            offset = max(0, int(arg))
        except ValueError:
            offset = 0
        batch = favs[offset : offset + FAV_BATCH]
        if offset + FAV_BATCH < len(favs):
            next_offset = offset + FAV_BATCH
    if not batch:
        await cb.answer()
        return
    if uid in playing_favs:
        await cb.answer(texts.say("fav_busy"), show_alert=True)
        return
    await cb.answer()
    playing_favs.add(uid)
    try:
        await favorites.play_favorites(bot, cb.message.chat.id, uid, batch)
    finally:
        playing_favs.discard(uid)
    if next_offset is not None:
        n = min(FAV_BATCH, len(favs) - next_offset)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=texts.say("fav_next", n=n), callback_data=f"favplay:all:{next_offset}")]
            ]
        )
        await cb.message.answer(texts.say("fav_more", n=len(favs) - next_offset), reply_markup=kb)


@router.message(F.voice | F.audio | F.video | F.video_note | F.document.mime_type.startswith("audio/"))
async def on_media(m: Message, bot: Bot):
    """Прислали музыку (голосовое, аудио, видео, кружок): узнаём песню и ищем её версии."""
    if not allowed(m.from_user.id):
        return
    await db.touch_user(m.from_user.id, m.from_user.full_name)
    media = m.voice or m.audio or m.video or m.video_note or m.document
    if media.file_size and media.file_size > 20 * 1024 * 1024:  # лимит Telegram на скачивание файлов ботом
        await m.answer(texts.say("input_too_big"))
        return
    status = await m.answer(texts.say("recognizing"))
    workdir = paths.DOWNLOAD_DIR / f"in_{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        src = workdir / "input"
        await bot.download(media, destination=src)
        rec = await recognize.recognize(src)
    except Exception:
        logging.exception("recognition failed")
        await status.edit_text(texts.say("recognize_error"))
        return
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not rec:
        await status.edit_text(texts.say("not_recognized"))
        return
    fields = {"artist": html.escape(rec.artist), "title": html.escape(rec.title)}
    try:
        outcome = await search.versions(rec.artist, rec.title)
    except Exception:
        logging.exception("versions search failed")
        await status.edit_text(texts.say("search_error"))
        return
    if not outcome.results:
        await status.edit_text(texts.say("versions_none", **fields), parse_mode=ParseMode.HTML)
        return
    await status.edit_text(
        texts.say("recognized", **fields), parse_mode=ParseMode.HTML, reply_markup=results_keyboard(outcome.results)
    )
    start_prefetch(outcome.results)


@router.message(F.text)
async def on_text(m: Message):
    if not allowed(m.from_user.id):
        return
    await db.touch_user(m.from_user.id, m.from_user.full_name)
    text = m.text.strip()
    url = download.extract_url(text)
    if url:
        if is_music_video(url):
            await run_link_music(m, url)
            return
        kb = link_keyboard(url)
        whole = playlist_bot.whole_playlist_button(url)  # песня из плейлиста: можно взять и весь список
        if whole:
            kb.inline_keyboard.append([whole])
        await m.answer(texts.say("ask_link"), reply_markup=kb)
        return

    await run_search(m, text)


def is_music_video(url: str) -> bool:
    """Ролики, где главное, как правило, музыка за кадром: сначала узнаём её, потом предлагаем скачать."""
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(h in host for h in ("tiktok.com", "tt.site", "instagram.com"))


def link_keyboard(url: str) -> InlineKeyboardMarkup:
    """Скачать ссылку как есть: звук из ролика или само видео."""
    return InlineKeyboardMarkup(inline_keyboard=link_buttons(url))


def link_buttons(url: str, audio_label: str | None = None) -> list[list[InlineKeyboardButton]]:
    key = remember(url)
    return [
        [
            InlineKeyboardButton(text=audio_label or texts.say("btn_audio"), callback_data=f"dl:a:{key}"),
            InlineKeyboardButton(text=texts.say("btn_video"), callback_data=f"dl:v:{key}"),
        ]
    ]


async def run_link_music(m: Message, url: str):
    """TikTok/Instagram: определяем музыку из ролика и показываем её версии. Кнопки скачать звук ролика
    или само видео остаются под списком."""
    status = await m.answer(texts.say("recognizing"))
    rec = None
    try:
        d = await download.download(url, "audio", None)  # звук ролика (у TikTok это «оригинальный звук»)
        try:
            rec = await recognize.recognize(d.path)
        finally:
            d.cleanup()
    except Exception:
        logging.exception("link recognition failed")
    raw = link_buttons(url, texts.say("btn_original_sound"))
    if not rec:
        await status.edit_text(texts.say("link_not_recognized"), reply_markup=InlineKeyboardMarkup(inline_keyboard=raw))
        return
    fields = {"artist": html.escape(rec.artist), "title": html.escape(rec.title)}
    try:
        outcome = await search.versions(rec.artist, rec.title)
    except Exception:
        logging.exception("versions search failed")
        outcome = None
    if not outcome or not outcome.results:
        await status.edit_text(
            texts.say("versions_none", **fields),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=raw),
        )
        return
    await status.edit_text(
        texts.say("recognized", **fields),
        parse_mode=ParseMode.HTML,
        reply_markup=results_keyboard(outcome.results, extra_rows=raw),
    )
    start_prefetch(outcome.results)


async def run_search(m: Message, text: str):
    wait = await m.answer(texts.say("searching"))
    try:
        outcome = await search.search(text)
    except Exception:
        logging.exception("search failed")
        await wait.edit_text(texts.say("search_error"))
        return
    results = outcome.results
    deep_key = remember_query(outcome.query or text)
    if not results:
        deep_kb = results_keyboard([], deep_key)
        if outcome.hint:
            await wait.edit_text(texts.say("catalog_hint", hint=outcome.hint), reply_markup=deep_kb)
        else:
            await wait.edit_text(texts.say("nothing_deep"), reply_markup=deep_kb)
        return
    is_artist = outcome.kind == "artist"
    header, parse_mode = (texts.say("artist_found", artist=outcome.artist) if is_artist else texts.say("found")), None
    if not is_artist and results[0].verified:
        # песня найдена: сначала показываем все её версии (оригинал, ремиксы, live, slowed, sped up, каверы…)
        first = results[0]
        await wait.edit_text(texts.say("searching_versions"))
        try:
            vers = await search.versions(first.artist, first.title)
        except Exception:
            logging.exception("versions search failed")
            vers = None
        if vers and len(vers.results) >= 2:
            results = vers.results
            header = texts.say("versions_found", title=html.escape(first.title), artist=html.escape(first.artist))
            parse_mode = ParseMode.HTML
    await wait.edit_text(
        header, parse_mode=parse_mode, reply_markup=results_keyboard(results, deep_key, more=is_artist)
    )
    start_prefetch(results)


def remember_query(q: str) -> str:
    key = uuid.uuid4().hex[:10]
    deep_queries[key] = q
    if len(deep_queries) > 500:
        deep_queries.pop(next(iter(deep_queries)))
    return key


def results_keyboard(
    results: list[download.SearchResult],
    deep_key: str | None = None,
    more: bool = False,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    for r in results:
        key = remember(r.url, r)
        who = f"{r.artist[:20]} — " if r.artist else ""
        tag = f"{r.label}: " if r.label and r.label != "Original" else ""
        label = f"{search.ICONS.get(r.source, '🎵')} {tag}{who}{r.title}"[:50]
        if r.duration:
            label += f" · {fmt_dur(r.duration)}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"dl:a:{key}")])
    if deep_key:
        label = texts.say("more_button" if more else "deep_button")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"deep:{deep_key}")])
    rows.extend(extra_rows or [])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("deep:"))
async def on_deep(cb: CallbackQuery):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    query = deep_queries.get(cb.data.split(":", 1)[1])
    if not query:
        await cb.answer(texts.say("expired"), show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text(texts.say("deep_searching"))
    try:
        outcome = await search.search(query, deep=True)
    except Exception:
        logging.exception("deep search failed")
        await cb.message.edit_text(texts.say("search_error"))
        return
    if not outcome.results:
        await cb.message.edit_text(texts.say("deep_nothing"))
        return
    await cb.message.edit_text(texts.say("deep_found"), reply_markup=results_keyboard(outcome.results))
    start_prefetch(outcome.results)


fav_keyboard = favorites.fav_keyboard  # кнопка «в избранное» под присланной песней


@router.callback_query(F.data.startswith("fav:"))
async def on_favorite(cb: CallbackQuery):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    try:
        play_id = int(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer()
        return
    play = await db.get_play(play_id)
    if not play or play["user_id"] != cb.from_user.id:
        await cb.answer(texts.say("expired"), show_alert=True)
        return
    uid = cb.from_user.id
    if await db.fav_has(uid, play["artist"], play["title"]):
        await db.fav_remove(uid, play["artist"], play["title"])
        now_fav, note = False, texts.say("fav_removed")
    else:
        await db.fav_add(uid, play["artist"], play["title"], play["duration"], play["cover"])
        # заранее сохраняем звук на диск: дальше песня будет играть мгновенно и больше не скачивается заново
        favcache.spawn(favcache.prefetch(uid, play["artist"], play["title"], play["duration"], play["cover"]))
        now_fav, note = True, texts.say("fav_added")
    await cb.answer(note)
    try:
        await cb.message.edit_reply_markup(reply_markup=fav_keyboard(play_id, now_fav))
    except Exception:
        pass  # сообщение могло быть удалено или кнопка не изменилась


@router.callback_query(F.data.startswith("dl:"))
async def on_download(cb: CallbackQuery, bot: Bot):
    if not allowed(cb.from_user.id):
        await cb.answer()
        return
    _, k, key = cb.data.split(":", 2)
    entry = pending.get(key)
    if not entry:
        await cb.answer(texts.say("expired"), show_alert=True)
        return
    url, meta = entry
    kind = "audio" if k == "a" else "video"
    await cb.answer(texts.say("downloading_short"))
    status = await cb.message.answer(texts.say("downloading"))

    cache_key = f"{kind}:{url}"
    cached = await db.get_cached(cache_key)
    if cached and meta and meta.duration and cached[3] and abs(cached[3] - meta.duration) > 15:
        cached = None  # в кэше другая версия (не та длительность) — качаем заново
    try:
        cover = meta.thumb_url if meta else ""
        if cached:
            file_id, title, artist, duration = cached
            play_id = await db.log_play(cb.from_user.id, title, artist, duration, "cache", url, kind, cover)
            if kind == "audio":
                await bot.send_audio(
                    cb.message.chat.id,
                    file_id,
                    title=title,
                    performer=artist,
                    duration=duration,
                    reply_markup=fav_keyboard(play_id, await db.fav_has(cb.from_user.id, artist, title)),
                )
            else:
                await bot.send_video(cb.message.chat.id, file_id, duration=duration)
        else:
            if kind == "audio" and url in prefetch:
                try:
                    d = await prefetch.pop(url)[1]  # уже скачано или досасывается
                except Exception as e:  # заранее не вышло — пробуем ещё раз, сейчас
                    logging.info("предзагрузка не удалась (%s), качаем заново: %s", url[:80], str(e)[:120])
                    d = await search.fetch_audio(url, meta)
            elif kind == "audio":
                d = await search.fetch_audio(url, meta)  # при сбое сам пробует другие версии и запасные источники
            else:
                d = await download.download(url, kind, meta)
            try:
                thumb = FSInputFile(d.thumb) if d.thumb else None
                play_id = await db.log_play(cb.from_user.id, d.title, d.artist, d.duration, d.source, url, kind, cover)
                if kind == "audio":
                    sent = await bot.send_audio(
                        cb.message.chat.id,
                        FSInputFile(d.path),
                        title=d.title,
                        performer=d.artist,
                        duration=d.duration,
                        thumbnail=thumb,
                        reply_markup=fav_keyboard(play_id, await db.fav_has(cb.from_user.id, d.artist, d.title)),
                    )
                    file_id = sent.audio.file_id
                else:
                    sent = await bot.send_video(
                        cb.message.chat.id,
                        FSInputFile(d.path),
                        caption=d.title[:200],
                        duration=d.duration,
                        thumbnail=thumb,
                        supports_streaming=True,
                    )
                    file_id = sent.video.file_id
                await db.set_cached(cache_key, file_id, d.title, d.artist, d.duration)
            finally:
                d.cleanup()
        await status.delete()
    except Exception as e:
        logging.exception("download failed")
        await status.edit_text(texts.say("too_big" if "50 MB" in str(e) else "dl_fail"))


async def set_menu_button(bot: Bot, chat_id: int | None = None) -> None:
    """Кнопка меню с дашбордом; для chat_id — личная, с подписью на языке пользователя."""
    if not webapp_url:
        return
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(text=texts.say("menu_button"), web_app=WebAppInfo(url=webapp_url)),
        )
    except Exception:
        logging.exception("не удалось установить кнопку меню")


async def setup_webapp(bot: Bot, bot_username: str):
    """Поднимает локальный сервер дашборда, туннель (если нет своего адреса) и кнопку меню."""
    global webapp_url
    runner = await webapp.start(bot, TOKEN, bot_username, ALLOWED, WEBAPP_PORT)
    tun = None
    if not webapp_url:
        tun = await tunnel.start(WEBAPP_PORT)
        if tun:
            webapp_url = tun[1]
    if not webapp_url:
        logging.warning("Дашборд работает только локально: нет cloudflared и WEBAPP_URL")
        return runner, None
    await set_menu_button(bot)
    logging.info("Дашборд: %s", webapp_url)
    return runner, (tun[0] if tun else None)


async def main():
    logging.basicConfig(level=logging.INFO)
    download.init()
    if not TOKEN:
        raise SystemExit("Нет BOT_TOKEN в .env")
    await db.init_db()
    warm_task = asyncio.create_task(search.keep_warm())  # noqa: F841 — прогрев: поиск идёт по готовым соединениям
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.update.outer_middleware(LangMiddleware())
    dp.include_router(playlist_bot.router)  # раньше общего: ссылки на плейлисты и пересланные песни
    dp.include_router(router)
    me = await bot.get_me()
    logging.info("Бот запущен: @%s", me.username)
    try:
        for code in texts.LANGS:  # английский — для всех остальных; ru и it — тем, у кого Telegram на этом языке
            scope = None if code == texts.DEFAULT else code
            await bot.set_my_commands(
                [
                    BotCommand(command=cmd, description=texts.say(key, code))
                    for cmd, key in [
                        ("favorites", "cmd_favorites"),
                        ("app", "cmd_app"),
                        ("stats", "cmd_stats"),
                        ("settings", "cmd_settings"),
                        ("help", "cmd_help"),
                        ("start", "cmd_start"),
                    ]
                ],
                language_code=scope,
            )
            await bot.set_my_description(texts.say("bot_description", code), language_code=scope)
            await bot.set_my_short_description(texts.say("bot_short", code), language_code=scope)
    except Exception:
        logging.exception("не удалось обновить описание бота")
    runner, tunnel_proc = await setup_webapp(bot, me.username)
    try:
        await dp.start_polling(bot)
    finally:
        if tunnel_proc and tunnel_proc.returncode is None:
            tunnel_proc.terminate()
        await runner.cleanup()  # закрывает и общий ClientSession мини-аппа (on_cleanup)
