# MusicBot

[![CI](https://github.com/shaygrizz/MusicBot/actions/workflows/ci.yml/badge.svg)](https://github.com/shaygrizz/MusicBot/actions/workflows/ci.yml)

Telegram bot that finds and delivers music, with a companion Mini App dashboard for favorites, stats and playback.

Search by title, artist, a remembered lyric line, a link, or a voice/video message. Typos are tolerated.

## Features

- **Smart search.** Queries run across YouTube Music, SoundCloud, Audius and catalog APIs (Deezer, iTunes) in parallel and are verified against the catalog.
- **Exact tracks first.** Original studio versions rank above covers, remixes, live takes and truncated uploads. Ambiguous titles resolve to the most popular song.
- **Lyrics and typo tolerance.** Find a song from a line you remember; misspelled titles and artist names are corrected.
- **Recognition.** Identify a song from voice, audio, video, or a TikTok/Instagram link (ShazamIO).
- **Resilient downloads.** Each track falls back through multiple sources, and the downloaded file length is checked against the catalog.
- **Playlists and albums.** Send a YouTube, YouTube Music or Spotify playlist or album link: the bot lists the songs, sends them to the chat in batches (with Stop) or adds them all to favorites.
- **Songs from other bots.** Forward audio from other bots or chats; the bot collects them and adds them to favorites, playing them from Telegram's own copy.
- **Mini App dashboard.** Favorites, listening stats, a full player with repeat, and hand-off to chat playback.
- **Languages.** English, Russian and Italian in the bot and the Mini App. `/start` asks for the language first;
  it can be changed later in `/settings` or in the Mini App profile (one choice for both).

## Stack

Python 3.12 · aiogram 3 · yt-dlp · ytmusicapi · aiohttp · SQLite · ffmpeg

## Quick start

```bash
git clone https://github.com/shaygrizz/MusicBot.git
cd MusicBot
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # set BOT_TOKEN
python -m musicbot
```

`run.sh` (Linux/macOS) and `run.bat` (Windows) do the same in one step.
Without `WEBAPP_URL`, the bot opens a Cloudflare tunnel for the dashboard automatically.
Song recognition additionally needs `shazamio` (see `requirements-docker.txt`).

## Configuration

| Variable | Purpose |
|---|---|
| `BOT_TOKEN` | Token from @BotFather (required) |
| `ALLOWED_USERS` | Comma-separated Telegram IDs allowed to use the bot; empty means everyone |
| `ADMIN_USERS` | Telegram IDs with access to `/admin` |
| `WEBAPP_URL` | Permanent HTTPS address of the dashboard; empty starts a tunnel |
| `AUDIO_QUALITY` | `fast` for smaller, quicker files |
| `DATA_DIR` | Where the database, avatars, favorites cache and `cookies.txt` live |
| `SEARCH_FAST_DEADLINE` | Seconds to wait for fast sources (default `3`); raise it on slow hosts |
| `PLAYLIST_BATCH` | Songs sent per tap from a playlist (default `10`) |
| `PLAYLIST_MAX` | Most songs read from one playlist link (default `200`) |
| `PLAYLIST_JOBS` | Playlists sent at the same time across all users (default `2`) |
| `FAV_CACHE_MB` | Disk space for the favorites audio cache (default `1500`) |

## Project layout

```
musicbot/
  bot.py            Telegram handlers and startup
  playlist_bot.py   playlist links and forwarded songs (handlers)
  playlists.py      reading YouTube, YouTube Music and Spotify playlists
  search/           queries, catalogs, exact-track ranking, lyrics, versions, fetch with fallbacks
  download/         fallback chain, YouTube access, media checks
  providers/        hitmotop and Audius clients
  web/              Mini App server (app.py) and page (static/)
  db.py, paths.py, favorites.py, favcache.py, recognize.py, tunnel.py, texts.py
tests/              offline unit tests
docs/               architecture notes
```

See [docs/architecture.md](docs/architecture.md) for how the parts fit together.

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
```

Tests run offline: catalog and source responses are faked, so they are fast and stable.

## Deployment

A `Dockerfile` is included (`amvera.yaml` is left over from the previous host, Amvera). The image bundles Python 3.12, Node.js (yt-dlp's JS runtime) and ffmpeg. The bot now runs on Bothost: mount persistent storage at `/data` and set `BOT_TOKEN` and the other variables (`DATA_DIR`, `WEBAPP_HOST`, `WEBAPP_PORT`, `WEBAPP_URL`, `ADMIN_USERS`, `SEARCH_FAST_DEADLINE`) in the host's settings.

YouTube often blocks data-center IPs. If downloads fail with a "not a bot" error, place a `cookies.txt` exported from a logged-in browser in `DATA_DIR`.

## Disclaimer

A personal project. Some fallback sources are third-party aggregators that are not licensed by rights holders; use the bot only for content you have the right to download, and respect each service's terms.

## License

[MIT](LICENSE)
