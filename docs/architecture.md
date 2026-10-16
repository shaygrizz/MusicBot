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
| repeated search | in memory, then SQLite (`search_cache`) after a restart; identical searches running at once share one call; a partial answer (YouTube Music timed out, catalog-only fallback) is kept 10 minutes in memory only |
| first search | shared keep-alive HTTP session (`net.py`), sources kept warm (`search/warm.py`); answers as soon as the fast sources found the exact track instead of waiting for every deadline |
| repeated download | shared disk cache (`audiocache.py`, `DATA_DIR/audio`, `AUDIO_CACHE_MB`), keyed by the source link and the catalog link (hard links, one file on disk), for every user |
| first click in the bot | the top result is downloaded right after the search, catalog songs included |
| Mini App sheet | versions come from the fast sources only; opening the sheet extracts the audio link of the top versions (no full download: it would compete with the stream for bandwidth); **Play** streams through `/api/stream/{tok}`, a proxy with `Range` support (the YouTube link is bound to the server's IP); a stream that ran from the first to the last byte is remuxed, length-checked and stored in the shared cache, so the song is fetched from YouTube once; if the WebView reports an error or does not start within 7 s, the page falls back to the whole file |
| download pipeline | the cover downloads in parallel with the audio; the length check reads the header with `mutagen` and runs `ffprobe` only as a fallback |

YouTube link extractions are limited to `YT_EXTRACT_PARALLEL` (3) at once: warming and prefetching request links in bursts, and
too many raise the risk of the "not a bot" IP block.

A stream that started through the proxy keeps using it: the cached file is remuxed and its bytes differ, so a seek
must not switch sources mid-play.

## Finding a version by name (`search/fetch.py: resolve`)

Catalog songs (Spotify, Deezer, favorites) have no YouTube link, so a version is looked up by name. The search goes
from narrow to wide and stops at the first version that passes every check:

1. main artist + title ("Gorillaz Clint Eastwood": with the guests in the query YouTube Music hides the album cut);
2. all artists + title;
3. title alone, for names of five letters or more (the artist is spelled differently on the platform);
4. a full search with the slow sources;
5. the same results again past the cache, if a fast source timed out;
6. as a last resort, the length check relaxed to 20 s or 12 % (radio edit vs album). Such a version is marked
   `loose`, and the downloaded file is compared with that version's own length.

Checks (`reject_reason`), each strict on its own: the title without catalog tails ("- Remastered 2009",
"- From …", "- Radio Edit", "- Mixed", "+ Guest"; also in Latin letters); a whole artist name, also across alphabets
("Kino" / "Кино") and close spellings for names of five letters or more, short names only exactly; no remix, cover,
sped-up or similar marker unless asked for; length within 8 s. "- Mixed" tracks skip the length check because the
catalog gives the length of the cut inside the mix.

A miss is logged with the closest results and why each was refused. `python -m musicbot.check_playlist <url>` runs the
same search for a whole playlist without downloading and prints every miss with its reasons; run it on the server,
since YouTube Music shows different catalogues by country.

## Playlists (`playlists.py`, `playlist_bot.py`)

A playlist or album link is read into a list of tracks (artist, title, duration, YouTube link when known); nothing is
downloaded at that point. Each track then goes through the usual path, one at a time.

| Source | How the list is read |
|---|---|
| YouTube / YouTube Music playlist | `ytmusicapi.get_playlist` (clean names), falling back to yt-dlp without downloading (raw titles, cleaned) |
| YouTube Music album (`OLAK5uy_…`, `browse/MPRE…`) | `ytmusicapi.get_album` |
| Spotify playlist or album | the public embed page, no developer key; at most 100 tracks, and the bot says when the list was cut |

Tracks with a YouTube link are downloaded by that link; the others are found by name like catalog songs. Auto-mixes
(`RD…`), Liked and Watch Later are refused. A song shared from a playlist (`watch?v=…&list=…`) stays a song, with an
extra "Whole playlist" button.

Sending reuses the favorites sender (`favorites.send_tracks`): batches of `PLAYLIST_BATCH`, in reverse so the first
song plays first, the next song prepared while the current one uploads, a wait-and-retry when Telegram answers
"retry after", and a Stop button checked between songs. One playlist per user and `PLAYLIST_JOBS` across all users.

Audio forwarded from other bots or chats is not recognized again: the bot collects the files for two seconds of quiet,
shows one message, and on "Add to favorites" stores each Telegram `file_id` under the same cache key favorites use, so
the songs play in chat without touching YouTube. Files up to 20 MB are also copied into the favorites cache for the
Mini App. Forwarded audio without a title falls through to recognition.

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
