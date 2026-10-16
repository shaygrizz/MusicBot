"""All user-facing bot messages in one place. Short and neutral."""

LINES: dict[str, str] = {
    "start": (
        "Send a track name, a line from a song, or a link (YouTube, TikTok, Instagram, SoundCloud).\n"
        "Playlists and albums from YouTube, YouTube Music and Spotify work too, "
        "and you can forward songs from other bots to save them.\n\n"
        "/app dashboard\n"
        "/stats your stats\n"
        "/help usage"
    ),
    "help": (
        "Send a track name, lyrics or a link, then choose a result.\n"
        "Links offer audio or video.\n"
        "A playlist or album link (YouTube, YouTube Music, Spotify) lists its songs: send them to the chat "
        "or add them all to favorites.\n"
        "Forward songs from other bots to add them to your favorites.\n\n"
        "/app dashboard\n"
        "/stats your stats"
    ),
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


def say(key: str, **fmt) -> str:
    return LINES[key].format(**fmt)
