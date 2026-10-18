"""All user-facing bot messages in one place, in every supported language. Short and neutral.

say() takes the language of the user whose update is being handled (set once per update by the bot's middleware
through use()), so the handlers do not pass it around."""

from contextvars import ContextVar

DEFAULT = "en"
# code -> (flag, name in its own language)
LANGS: dict[str, tuple[str, str]] = {
    "en": ("🇬🇧", "English"),
    "ru": ("🇷🇺", "Русский"),
    "it": ("🇮🇹", "Italiano"),
}

_current: ContextVar[str] = ContextVar("lang", default=DEFAULT)

EN: dict[str, str] = {
    "start": (
        "Send a track name, a line from a song, or a link (YouTube, TikTok, Instagram, SoundCloud).\n"
        "Playlists and albums from YouTube, YouTube Music and Spotify work too, "
        "and you can forward songs from other bots to save them.\n\n"
        "/app dashboard\n"
        "/stats your stats\n"
        "/settings language\n"
        "/help usage"
    ),
    "help": (
        "Send a track name, lyrics or a link, then choose a result.\n"
        "Links offer audio or video, or ⌕ Find song: all versions of the song in the video.\n"
        "Under a list of versions, ☰ All versions looks for every variant (slowed, reverb, full, short…).\n"
        "A playlist or album link (YouTube, YouTube Music, Spotify) lists its songs: send them to the chat "
        "or add them all to favorites.\n"
        "Forward songs from other bots to add them to your favorites.\n\n"
        "/app dashboard\n"
        "/stats your stats\n"
        "/settings language"
    ),
    "lang_choose": "Choose your language\nВыберите язык\nScegli la lingua",
    "lang_set": "Language: {flag} {name}",
    "settings_title": "<b>Settings</b>",
    "settings_lang": "Language: {flag} {name}",
    "settings_back": "← Back",
    "unknown": "unknown",
    "searching": "Searching…",
    "searching_versions": "Searching versions…",
    "versions_found": "Versions of <b>{title}</b> by {artist}:",
    "link_not_recognized": "Could not identify the music in this video. Choose what to download:",
    "btn_original_sound": "Original sound",
    "fav_title": "<b>My Favorites</b>, {n} tracks",
    "fav_empty": "No favorites yet. Tap ☆ under any song to add it.",
    "fav_play_all": "▶ Play all",
    "fav_shuffle": "🔀 Shuffle",
    "fav_send": "⬇ Send files to chat",
    "fav_next": "▶ Next {n}",
    "fav_progress": "Sending {i}/{n}…",
    "fav_busy": "Already playing your favorites.",
    "fav_more": "{n} more tracks in your favorites.",
    "fav_rest": "...and {n} more. Tap Play all to hear everything.",
    "kb_favorites": "▶ My Favorites",
    "kb_dashboard": "Dashboard",
    "cmd_favorites": "Play my favorites",
    "fav_add": "☆ Add to favorites",
    "fav_remove": "★ In favorites",
    "fav_added": "Added to favorites",
    "fav_removed": "Removed from favorites",
    "recognizing": "Listening…",
    "recognized": "Recognized: <b>{artist}</b>, {title}\nVersions:",
    "versions_none": "Recognized: <b>{artist}</b>, {title}\nNo downloadable versions found.",
    "not_recognized": "Could not recognize this audio. Try a clearer fragment of 10+ seconds.",
    "input_too_big": "File is too large to process (Telegram limit: 20 MB).",
    "recognize_error": "Recognition failed. Try again later.",
    "found": "Results:",
    "artist_found": "Top tracks by {artist}:",
    "more_button": "🟠 More Tracks",
    "nothing": "No results. Try artist and title, or send a link.",
    "nothing_deep": "No results.",
    "catalog_hint": 'Found "{hint}" in the catalog, but no download source. Try sending a link.',
    "search_error": "Search failed. Try again.",
    "ask_link": "Choose format:",
    "btn_audio": "Audio",
    "btn_video": "Video",
    "btn_find_song": "⌕ Find song",
    "btn_all_versions": "☰ All versions",
    "downloading": "Downloading…",
    "downloading_short": "Downloading…",
    "expired": "This button has expired. Send your request again.",
    "dl_fail": "Download failed. The link may be restricted. Try another.",
    "too_big": "File is larger than 50 MB (Telegram limit).",
    "stats_title": "<b>Your stats</b>",
    "stats_tracks": "Tracks: <b>{n}</b>",
    "stats_time": "Listening time: <b>{hours:.1f} h</b>",
    "stats_top_artists": "\nTop artists:",
    "stats_top_tracks": "\nTop tracks:",
    "stats_empty": "No activity yet. Search for a track to start.",
    "app_text": "Open your dashboard.",
    "app_button": "Open dashboard",
    "app_unavailable": "Dashboard is unavailable right now. Try again later.",
    "menu_button": "Dashboard",
    "deep_button": "🟠 Search Deeper",
    "deep_searching": "Searching deeper…",
    "deep_found": "Extended results:",
    "deep_nothing": "Nothing found. Try sending a link.",
    "bot_description": "Find and download music by name, lyrics or link.",
    "bot_short": "Music finder and downloader",
    "cmd_app": "Dashboard",
    "cmd_stats": "Your stats",
    "cmd_settings": "Settings and language",
    "cmd_help": "How to use",
    "cmd_start": "Start",
    "pl_reading": "Reading playlist…",
    "pl_read_fail": "Could not read this playlist. It may be private or the link may be wrong.",
    "pl_empty": "This playlist has no tracks that can be downloaded.",
    "pl_title": "<b>{title}</b>, {n} tracks",
    "pl_cut": "Only the first {n} tracks are available from this link.",
    "pl_rest": "...and {n} more.",
    "pl_send": "⬇ Send to chat ({n})",
    "pl_fav": "☆ Add all {n} to favorites",
    "pl_cancel": "✕ Cancel",
    "pl_whole": "📃 Whole playlist",
    "pl_stop": "■ Stop",
    "pl_stopping": "Stopping…",
    "pl_stopped": "Stopped.",
    "pl_queued": "Waiting in line: other playlists are being sent right now…",
    "pl_busy": "Already sending a playlist. Wait for it to finish or tap Stop.",
    "pl_done": "Sent {sent} of {n}.",
    "pl_failed": "{n} could not be found or downloaded.",
    "pl_left": "{n} tracks left.",
    "pl_next": "▶ Next {n}",
    "pl_send_fail": "Sending the playlist failed. Try again.",
    "pl_fav_done": "Added {n} of {total} to favorites (the rest were already there). They download in the background.",
    "in_title": "<b>Received {n} tracks</b>",
    "in_fav": "☆ Add {n} to favorites",
    "in_done": "Added {n} of {total} tracks to favorites.",
}

RU: dict[str, str] = {
    "start": (
        "Пришлите название трека, строчку из песни или ссылку (YouTube, TikTok, Instagram, SoundCloud).\n"
        "Плейлисты и альбомы из YouTube, YouTube Music и Spotify тоже работают, "
        "а песни из других ботов можно переслать сюда, чтобы сохранить.\n\n"
        "/app дашборд\n"
        "/stats ваша статистика\n"
        "/settings язык\n"
        "/help как пользоваться"
    ),
    "help": (
        "Пришлите название трека, текст песни или ссылку и выберите результат.\n"
        "По ссылке можно взять аудио, видео или ⌕ найти песню: все версии песни из ролика.\n"
        "Под списком версий кнопка ☰ Все версии ищет все варианты (slowed, reverb, полные, неполные…).\n"
        "Ссылка на плейлист или альбом (YouTube, YouTube Music, Spotify) покажет список песен: "
        "их можно отправить в чат или добавить все в избранное.\n"
        "Песни, пересланные из других ботов, добавляются в избранное.\n\n"
        "/app дашборд\n"
        "/stats ваша статистика\n"
        "/settings язык"
    ),
    "lang_choose": "Choose your language\nВыберите язык\nScegli la lingua",
    "lang_set": "Язык: {flag} {name}",
    "settings_title": "<b>Настройки</b>",
    "settings_lang": "Язык: {flag} {name}",
    "settings_back": "← Назад",
    "unknown": "неизвестно",
    "searching": "Ищу…",
    "searching_versions": "Ищу версии…",
    "versions_found": "Версии <b>{title}</b>, {artist}:",
    "link_not_recognized": "Не удалось определить музыку в этом видео. Выберите, что скачать:",
    "btn_original_sound": "Оригинальный звук",
    "fav_title": "<b>Избранное</b>, треков: {n}",
    "fav_empty": "В избранном пока пусто. Нажмите ☆ под любой песней, чтобы добавить её.",
    "fav_play_all": "▶ Играть всё",
    "fav_shuffle": "🔀 Вперемешку",
    "fav_send": "⬇ Отправить файлы в чат",
    "fav_next": "▶ Дальше {n}",
    "fav_progress": "Отправляю {i}/{n}…",
    "fav_busy": "Избранное уже отправляется.",
    "fav_more": "Ещё треков в избранном: {n}.",
    "fav_rest": "...и ещё {n}. Нажмите «Играть всё», чтобы послушать всё.",
    "kb_favorites": "▶ Избранное",
    "kb_dashboard": "Дашборд",
    "cmd_favorites": "Включить избранное",
    "fav_add": "☆ В избранное",
    "fav_remove": "★ В избранном",
    "fav_added": "Добавлено в избранное",
    "fav_removed": "Убрано из избранного",
    "recognizing": "Слушаю…",
    "recognized": "Узнал: <b>{artist}</b>, {title}\nВерсии:",
    "versions_none": "Узнал: <b>{artist}</b>, {title}\nСкачиваемых версий не нашлось.",
    "not_recognized": "Не удалось распознать звук. Попробуйте более чёткий фрагмент от 10 секунд.",
    "input_too_big": "Файл слишком большой для обработки (лимит Telegram: 20 МБ).",
    "recognize_error": "Не удалось распознать. Попробуйте позже.",
    "found": "Результаты:",
    "artist_found": "Лучшие треки, {artist}:",
    "more_button": "🟠 Ещё треки",
    "nothing": "Ничего не найдено. Попробуйте указать исполнителя и название или пришлите ссылку.",
    "nothing_deep": "Ничего не найдено.",
    "catalog_hint": "«{hint}» есть в каталоге, но источника для скачивания нет. Попробуйте прислать ссылку.",
    "search_error": "Поиск не удался. Попробуйте ещё раз.",
    "ask_link": "Выберите формат:",
    "btn_audio": "Аудио",
    "btn_video": "Видео",
    "btn_find_song": "⌕ Искать песню",
    "btn_all_versions": "☰ Все версии",
    "downloading": "Скачиваю…",
    "downloading_short": "Скачиваю…",
    "expired": "Эта кнопка устарела. Отправьте запрос заново.",
    "dl_fail": "Не удалось скачать. Возможно, ссылка ограничена. Попробуйте другую.",
    "too_big": "Файл больше 50 МБ (лимит Telegram).",
    "stats_title": "<b>Ваша статистика</b>",
    "stats_tracks": "Треков: <b>{n}</b>",
    "stats_time": "Время прослушивания: <b>{hours:.1f} ч</b>",
    "stats_top_artists": "\nЛюбимые исполнители:",
    "stats_top_tracks": "\nЛюбимые треки:",
    "stats_empty": "Пока нет активности. Найдите трек, чтобы начать.",
    "app_text": "Откройте свой дашборд.",
    "app_button": "Открыть дашборд",
    "app_unavailable": "Дашборд сейчас недоступен. Попробуйте позже.",
    "menu_button": "Дашборд",
    "deep_button": "🟠 Искать глубже",
    "deep_searching": "Ищу глубже…",
    "deep_found": "Расширенные результаты:",
    "deep_nothing": "Ничего не найдено. Попробуйте прислать ссылку.",
    "bot_description": "Находит и скачивает музыку по названию, тексту или ссылке.",
    "bot_short": "Поиск и загрузка музыки",
    "cmd_app": "Дашборд",
    "cmd_stats": "Ваша статистика",
    "cmd_settings": "Настройки и язык",
    "cmd_help": "Как пользоваться",
    "cmd_start": "Начать",
    "pl_reading": "Читаю плейлист…",
    "pl_read_fail": "Не удалось прочитать плейлист. Возможно, он закрыт или ссылка неверная.",
    "pl_empty": "В этом плейлисте нет треков, которые можно скачать.",
    "pl_title": "<b>{title}</b>, треков: {n}",
    "pl_cut": "По этой ссылке доступны только первые {n} треков.",
    "pl_rest": "...и ещё {n}.",
    "pl_send": "⬇ Отправить в чат ({n})",
    "pl_fav": "☆ Добавить все {n} в избранное",
    "pl_cancel": "✕ Отмена",
    "pl_whole": "📃 Весь плейлист",
    "pl_stop": "■ Стоп",
    "pl_stopping": "Останавливаю…",
    "pl_stopped": "Остановлено.",
    "pl_queued": "Жду очереди: сейчас отправляются другие плейлисты…",
    "pl_busy": "Плейлист уже отправляется. Дождитесь конца или нажмите «Стоп».",
    "pl_done": "Отправлено {sent} из {n}.",
    "pl_failed": "Не удалось найти или скачать: {n}.",
    "pl_left": "Осталось треков: {n}.",
    "pl_next": "▶ Дальше {n}",
    "pl_send_fail": "Не удалось отправить плейлист. Попробуйте ещё раз.",
    "pl_fav_done": "Добавлено {n} из {total} в избранное (остальные уже были). Они скачиваются в фоне.",
    "in_title": "<b>Получено треков: {n}</b>",
    "in_fav": "☆ Добавить {n} в избранное",
    "in_done": "Добавлено {n} из {total} треков в избранное.",
}

IT: dict[str, str] = {
    "start": (
        "Invia il nome di un brano, una frase di una canzone o un link (YouTube, TikTok, Instagram, SoundCloud).\n"
        "Funzionano anche le playlist e gli album di YouTube, YouTube Music e Spotify, "
        "e puoi inoltrare qui i brani di altri bot per salvarli.\n\n"
        "/app dashboard\n"
        "/stats le tue statistiche\n"
        "/settings lingua\n"
        "/help come si usa"
    ),
    "help": (
        "Invia il nome di un brano, il testo o un link, poi scegli un risultato.\n"
        "Con un link puoi scegliere audio, video o ⌕ cercare la canzone: tutte le versioni del brano nel video.\n"
        "Sotto l'elenco delle versioni, ☰ Tutte le versioni cerca ogni variante (slowed, reverb, complete, brevi…).\n"
        "Il link di una playlist o di un album (YouTube, YouTube Music, Spotify) mostra i brani: "
        "puoi inviarli in chat o aggiungerli tutti ai preferiti.\n"
        "I brani inoltrati da altri bot vengono aggiunti ai preferiti.\n\n"
        "/app dashboard\n"
        "/stats le tue statistiche\n"
        "/settings lingua"
    ),
    "lang_choose": "Choose your language\nВыберите язык\nScegli la lingua",
    "lang_set": "Lingua: {flag} {name}",
    "settings_title": "<b>Impostazioni</b>",
    "settings_lang": "Lingua: {flag} {name}",
    "settings_back": "← Indietro",
    "unknown": "sconosciuto",
    "searching": "Cerco…",
    "searching_versions": "Cerco le versioni…",
    "versions_found": "Versioni di <b>{title}</b>, {artist}:",
    "link_not_recognized": "Non sono riuscito a riconoscere la musica in questo video. Scegli cosa scaricare:",
    "btn_original_sound": "Audio originale",
    "fav_title": "<b>Preferiti</b>, {n} brani",
    "fav_empty": "Nessun preferito per ora. Tocca ☆ sotto un brano per aggiungerlo.",
    "fav_play_all": "▶ Riproduci tutto",
    "fav_shuffle": "🔀 Casuale",
    "fav_send": "⬇ Invia i file in chat",
    "fav_next": "▶ Altri {n}",
    "fav_progress": "Invio {i}/{n}…",
    "fav_busy": "I preferiti sono già in invio.",
    "fav_more": "Altri brani nei preferiti: {n}.",
    "fav_rest": "...e altri {n}. Tocca «Riproduci tutto» per ascoltare tutto.",
    "kb_favorites": "▶ Preferiti",
    "kb_dashboard": "Dashboard",
    "cmd_favorites": "Riproduci i preferiti",
    "fav_add": "☆ Aggiungi ai preferiti",
    "fav_remove": "★ Nei preferiti",
    "fav_added": "Aggiunto ai preferiti",
    "fav_removed": "Rimosso dai preferiti",
    "recognizing": "Ascolto…",
    "recognized": "Riconosciuto: <b>{artist}</b>, {title}\nVersioni:",
    "versions_none": "Riconosciuto: <b>{artist}</b>, {title}\nNessuna versione scaricabile trovata.",
    "not_recognized": "Non sono riuscito a riconoscere l'audio. Prova con un frammento più chiaro di almeno 10 secondi.",
    "input_too_big": "Il file è troppo grande da elaborare (limite di Telegram: 20 MB).",
    "recognize_error": "Riconoscimento non riuscito. Riprova più tardi.",
    "found": "Risultati:",
    "artist_found": "I brani migliori di {artist}:",
    "more_button": "🟠 Altri brani",
    "nothing": "Nessun risultato. Prova con artista e titolo, oppure invia un link.",
    "nothing_deep": "Nessun risultato.",
    "catalog_hint": "«{hint}» è nel catalogo, ma non c'è una fonte per scaricarlo. Prova a inviare un link.",
    "search_error": "Ricerca non riuscita. Riprova.",
    "ask_link": "Scegli il formato:",
    "btn_audio": "Audio",
    "btn_video": "Video",
    "btn_find_song": "⌕ Cerca la canzone",
    "btn_all_versions": "☰ Tutte le versioni",
    "downloading": "Scarico…",
    "downloading_short": "Scarico…",
    "expired": "Questo pulsante è scaduto. Invia di nuovo la richiesta.",
    "dl_fail": "Download non riuscito. Il link potrebbe essere limitato. Prova con un altro.",
    "too_big": "Il file supera i 50 MB (limite di Telegram).",
    "stats_title": "<b>Le tue statistiche</b>",
    "stats_tracks": "Brani: <b>{n}</b>",
    "stats_time": "Tempo di ascolto: <b>{hours:.1f} h</b>",
    "stats_top_artists": "\nArtisti preferiti:",
    "stats_top_tracks": "\nBrani preferiti:",
    "stats_empty": "Ancora nessuna attività. Cerca un brano per iniziare.",
    "app_text": "Apri la tua dashboard.",
    "app_button": "Apri la dashboard",
    "app_unavailable": "La dashboard non è disponibile al momento. Riprova più tardi.",
    "menu_button": "Dashboard",
    "deep_button": "🟠 Cerca più a fondo",
    "deep_searching": "Cerco più a fondo…",
    "deep_found": "Risultati estesi:",
    "deep_nothing": "Non ho trovato nulla. Prova a inviare un link.",
    "bot_description": "Trova e scarica musica per nome, testo o link.",
    "bot_short": "Cerca e scarica musica",
    "cmd_app": "Dashboard",
    "cmd_stats": "Le tue statistiche",
    "cmd_settings": "Impostazioni e lingua",
    "cmd_help": "Come si usa",
    "cmd_start": "Avvia",
    "pl_reading": "Leggo la playlist…",
    "pl_read_fail": "Non sono riuscito a leggere la playlist. Potrebbe essere privata o il link è sbagliato.",
    "pl_empty": "Questa playlist non ha brani scaricabili.",
    "pl_title": "<b>{title}</b>, {n} brani",
    "pl_cut": "Da questo link sono disponibili solo i primi {n} brani.",
    "pl_rest": "...e altri {n}.",
    "pl_send": "⬇ Invia in chat ({n})",
    "pl_fav": "☆ Aggiungi tutti i {n} ai preferiti",
    "pl_cancel": "✕ Annulla",
    "pl_whole": "📃 Playlist intera",
    "pl_stop": "■ Stop",
    "pl_stopping": "Mi fermo…",
    "pl_stopped": "Fermato.",
    "pl_queued": "In coda: sto inviando altre playlist…",
    "pl_busy": "Una playlist è già in invio. Aspetta che finisca o tocca Stop.",
    "pl_done": "Inviati {sent} su {n}.",
    "pl_failed": "Non trovati o non scaricati: {n}.",
    "pl_left": "Brani rimasti: {n}.",
    "pl_next": "▶ Altri {n}",
    "pl_send_fail": "Invio della playlist non riuscito. Riprova.",
    "pl_fav_done": "Aggiunti {n} di {total} ai preferiti (gli altri c'erano già). Si scaricano in background.",
    "in_title": "<b>Brani ricevuti: {n}</b>",
    "in_fav": "☆ Aggiungi {n} ai preferiti",
    "in_done": "Aggiunti {n} brani su {total} ai preferiti.",
}

LINES: dict[str, dict[str, str]] = {"en": EN, "ru": RU, "it": IT}


def use(lang: str | None) -> str:
    """Language for everything said while handling the current update (and tasks started from it)."""
    code = lang if lang in LANGS else DEFAULT
    _current.set(code)
    return code


def current() -> str:
    return _current.get()


def guess(language_code: str | None) -> str:
    """Telegram's interface language of the user -> one of ours (English when unknown)."""
    short = (language_code or "").lower().split("-")[0]
    return short if short in LANGS else DEFAULT


def say(key: str, lang: str | None = None, **fmt) -> str:
    table = LINES.get(lang or _current.get(), EN)
    return table.get(key, EN[key]).format(**fmt)


def variants(key: str) -> list[str]:
    """The same message in every language: reply-keyboard buttons must be recognised whatever language was chosen."""
    return list(dict.fromkeys(table.get(key, EN[key]) for table in LINES.values()))
