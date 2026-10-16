# Architecture

MusicBot is one Python process. The Telegram bot (aiogram) and the Mini App server (aiohttp) share an asyncio loop,
one SQLite database and one search and download core. Blocking calls (yt-dlp, HTTP) run in threads through
`asyncio.to_thread`.

```
Telegram user ─┬─ Bot (bot.py) ───────┬─ search/  ── catalogs (Deezer, iTunes, Yandex, Genius)
               │                      │           └─ audio sources (YouTube Music, SoundCloud, Audius, hitmotop)
               └─ Mini App (web/app) ─┤
                                      └─ download/ ── fallback chain, file checks
```

## Search (`musicbot/search`)

| Module | Role |
|---|---|
| `engine.py` | runs all sources in parallel with deadlines and assembles the result list |
| `sources.py` | downloadable results: YouTube Music, YouTube, SoundCloud, Audius, hitmotop |
| `catalogs.py` | metadata only: Deezer, iTunes, Yandex Music, Genius; artist lookup with typo tolerance |
| `exact.py` | decides whether a result is the exact original track |
| `ranking.py` | verifies results against the catalog, ranks, removes duplicates |
| `lyrics.py` | finds a song from a remembered lyric line (Genius) and corrects typos |
| `versions.py` | original plus remixes, live, slowed, acoustic, covers |
| `fetch.py` | picks a version and downloads it with retries and a length check |

**Exact track.** A result is exact only when its title (without `feat.` and `Official Audio`), full main artist and
length (within 8 s) match one clean catalog entry, and it carries no remix, live, cover or similar marker. The
reference is the most popular catalog entry, so a title shared by many songs resolves to the best known one.

**Song or artist.** If an artist has fewer than 200 thousand fans but a popular song carries the same name, the query is
a song. Names with typos match a well-known artist by similarity.

**Lyrics.** A song is accepted only if the matched lyric snippet is almost the whole query line, the song exists in
Deezer, and the query is not already "artist + title". With typos, words are corrected from found titles and only
well-known songs are accepted.

## Download (`musicbot/download`)

`chain.py` tries YouTube (parallel range download, plain yt-dlp, alternative clients), then falls back by title to
SoundCloud, hitmotop and dydki. After every download `fetch.py` compares the real file length with the catalog and
rejects files that are off by more than 20 s, so a wrong song is never delivered under the right title.

YouTube is skipped for two minutes after a real IP block ("not a bot"). A single 403 or 429 on one video does not
disable it for other songs.

## Speed

The first download of a song is bound by the network: about 2.5 s for yt-dlp to extract the audio link, plus the
transfer itself (parallel ranges do not help, the limit is bandwidth). Everything around that is cached or done ahead:

| What | How |
|---|---|
| repeated search | in memory, then SQLite (`search_cache`) after a restart; identical searches running at once share one call |
| first search | shared keep-alive HTTP session (`net.py`), sources kept warm (`search/warm.py`); answers as soon as the fast sources found the exact track instead of waiting for every deadline |
| repeated download | shared disk cache (`audiocache.py`, `DATA_DIR/audio`), keyed by the source link and the catalog link, for every user |
| first click in the bot | the top result is downloaded right after the search, catalog songs included |
| Mini App sheet | versions come from the fast sources only; the top version starts downloading when the sheet opens; **Play** streams through `/api/stream/{tok}` (a proxy with `Range` support, because the YouTube link is bound to the server's IP) and falls back to the whole file if the WebView cannot play the stream |
| download pipeline | the cover downloads in parallel with the audio; the length check reads the header with `mutagen` and runs `ffprobe` only as a fallback |

A stream that started through the proxy keeps using it: the cached file is remuxed and its bytes differ, so a seek
must not switch sources mid-play.

## Mini App (`musicbot/web`)

Every API request carries Telegram's signed `initData` in `X-Init-Data`. The server verifies the HMAC with the bot
token, rejects data older than a day and checks the allow-list. Files are served by unguessable tokens. The admin API
answers 404 to everyone outside `ADMIN_USERS`.

## Data

SQLite tables: `users`, `plays`, `favorites`, `cache` (Telegram `file_id`s), `search_cache`. The favorites audio
cache lives in `DATA_DIR/favorites`, the shared audio cache in `DATA_DIR/audio`. Paths come from `paths.py`, so the same code runs locally and in a container with
`DATA_DIR=/data`.

## Testing

`tests/` covers the pure logic and the failure modes found in practice: exact-track rules, ranking, lyrics and typo
handling, artist lookup, request signing, the download chain (including the YouTube block regression) and the web
server's access rules. External services are faked.
