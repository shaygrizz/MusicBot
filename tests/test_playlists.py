"""Плейлисты по ссылке и пересланные песни: разбор ссылок, чтение источников, отправка, избранное."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendAudio

from musicbot import db, favcache, favorites, playlist_bot, playlists, search


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.youtube.com/playlist?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i",
            ("youtube", "PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i"),
        ),
        (
            "https://music.youtube.com/playlist?list=OLAK5uy_l4UqNJCpAF3kNaV37LHdRc_A07MmVdiSU&si=x",
            ("youtube", "OLAK5uy_l4UqNJCpAF3kNaV37LHdRc_A07MmVdiSU"),
        ),
        ("https://music.youtube.com/browse/MPREb_TH6Wut5eTMQ", ("ytm_album", "MPREb_TH6Wut5eTMQ")),
        (
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc",
            ("spotify:playlist", "37i9dQZF1DXcBWIGoYBM5M"),
        ),
        ("https://open.spotify.com/intl-de/album/4yP0hdKOZPNshxUOjY0cZj", ("spotify:album", "4yP0hdKOZPNshxUOjY0cZj")),
        ("https://spotify.link/AbCdEf", ("spotify_short", "https://spotify.link/AbCdEf")),
        # одна песня, которой поделились из плейлиста, — это песня, а не плейлист
        ("https://www.youtube.com/watch?v=4NRXx6U8ABQ&list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i", None),
        ("https://youtu.be/4NRXx6U8ABQ?list=PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i", None),
        # автомиксы бесконечны, «Понравившиеся» и «Смотреть позже» — личные
        ("https://www.youtube.com/playlist?list=RDCLAK5uy_kmPRjHDECIcuVwnKsx2Ng7fyNgFKWNJFs", None),
        ("https://www.youtube.com/playlist?list=LL", None),
        ("https://www.youtube.com/playlist?list=WL", None),
        ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", None),
        ("https://www.youtube.com/watch?v=4NRXx6U8ABQ", None),
    ],
)
def test_playlist_links_are_told_apart_from_single_songs(url, expected):
    assert playlists.classify(url) == expected


def test_song_shared_from_a_playlist_offers_the_whole_list():
    lid = "PL4fGSI1pDJn6puJdseH2Rt9sMvt9E2M4i"
    assert playlists.playlist_of_single(f"https://www.youtube.com/watch?v=4NRXx6U8ABQ&list={lid}") == lid
    assert playlists.playlist_of_single(f"https://youtu.be/4NRXx6U8ABQ?list={lid}") == lid
    assert playlists.playlist_of_single("https://www.youtube.com/watch?v=4NRXx6U8ABQ&list=RD4NRXx6U8ABQ") is None
    assert playlists.playlist_of_single("https://www.youtube.com/watch?v=4NRXx6U8ABQ") is None


@pytest.mark.parametrize(
    ("raw", "channel", "expected"),
    [
        ("Shakira, Burna Boy - Dai Dai (Official Video)", "Shakira", ("Shakira, Burna Boy", "Dai Dai")),
        ("Blinding Lights", "The Weeknd - Topic", ("The Weeknd", "Blinding Lights")),
        ("Кино - Группа крови [Official Audio]", "Кино", ("Кино", "Группа крови")),
        ("Song (Remix)", "DJ", ("DJ", "Song (Remix)")),  # «Remix» — часть названия, его не трогаем
    ],
)
def test_youtube_titles_are_split_into_artist_and_song(raw, channel, expected):
    assert playlists.clean_youtube_title(raw, channel) == expected


def spotify_page(entity: dict) -> bytes:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'.encode()


def test_spotify_playlist_is_read_from_the_embed_page(monkeypatch):
    entity = {
        "name": "Today's Top Hits",
        "coverArt": {"sources": [{"url": "https://i.scdn.co/image/x"}]},
        "trackList": [
            {
                "title": "expectations (feat. Chappell Roan)",
                "subtitle": "Olivia Rodrigo,\xa0Chappell Roan",
                "duration": 223935,
            },
            {"title": "Nicole Kidman", "subtitle": "ADÉLA", "duration": 181270},
            {"title": "Gone", "subtitle": "X", "duration": 1000, "isPlayable": False},
        ],
    }
    monkeypatch.setattr(playlists.net, "get_bytes", lambda url, timeout=10: spotify_page(entity))
    pl = playlists.read("spotify:playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert pl.title == "Today's Top Hits" and not pl.cut
    assert [(t.artist, t.title, t.duration, t.url) for t in pl.tracks] == [
        ("Olivia Rodrigo, Chappell Roan", "expectations (feat. Chappell Roan)", 223, ""),
        ("ADÉLA", "Nicole Kidman", 181, ""),
    ]


def test_spotify_list_of_exactly_the_embed_limit_is_marked_as_cut(monkeypatch):
    entity = {"name": "Big", "trackList": [{"title": f"S{i}", "subtitle": "A", "duration": 1000} for i in range(100)]}
    monkeypatch.setattr(playlists.net, "get_bytes", lambda url, timeout=10: spotify_page(entity))
    assert playlists.read("spotify:playlist", "x" * 22).cut


def test_changed_spotify_page_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(playlists.net, "get_bytes", lambda url, timeout=10: b"<html>no data</html>")
    with pytest.raises(RuntimeError, match="Spotify"):
        playlists.read("spotify:album", "x" * 22)


class FakeYTM:
    def __init__(self, playlist=None, fail=False):
        self.playlist, self.fail = playlist, fail

    def get_playlist(self, list_id, limit=100):
        if self.fail:
            raise KeyError("contents")
        return self.playlist


def test_youtube_music_playlist_keeps_video_links(monkeypatch):
    ytm = FakeYTM(
        {
            "title": "Mix",
            "tracks": [
                {
                    "title": "Believer",
                    "artists": [{"name": "Imagine Dragons"}],
                    "duration_seconds": 204,
                    "videoId": "7wtfhZwyrcc",
                },
                {"title": "Gone", "artists": [], "duration_seconds": 100, "videoId": "x", "isAvailable": False},
                {"title": "Chart song", "artists": [{"name": "A"}], "duration_seconds": 180, "videoId": None},
            ],
        }
    )
    monkeypatch.setattr(playlists, "get_ytm", lambda: ytm)
    pl = playlists.read("youtube", "PLxxxxxxxxxx")
    assert [(t.artist, t.title, t.url) for t in pl.tracks] == [
        ("Imagine Dragons", "Believer", "https://www.youtube.com/watch?v=7wtfhZwyrcc"),
        ("A", "Chart song", ""),  # без ролика — найдётся по названию
    ]


def test_youtube_playlist_falls_back_to_yt_dlp(monkeypatch):
    monkeypatch.setattr(playlists, "get_ytm", lambda: FakeYTM(fail=True))
    monkeypatch.setattr(
        playlists, "_youtube_flat", lambda lid: playlists.Playlist("Flat", "youtube", [playlists.Track("A", "B", 1)])
    )
    assert playlists.read("youtube", "PLxxxxxxxxxx").title == "Flat"


class FakeBot:
    def __init__(self, retry_first=0):
        self.retry_first, self.sent, self.messages = retry_first, [], []

    async def send_audio(self, chat_id, audio, **kw):
        if self.retry_first:
            self.retry_first -= 1
            raise TelegramRetryAfter(SendAudio(chat_id=chat_id, audio="x"), "Too Many Requests", 0)
        self.sent.append(kw["title"])
        return SimpleNamespace(audio=SimpleNamespace(file_id=f"id-{kw['title']}"))

    async def send_message(self, chat_id, text, **kw):
        msg = SimpleNamespace(text=text, kw=kw, edits=[])

        async def edit_text(t, **k):
            msg.edits.append(t)

        async def delete():
            pass

        msg.edit_text, msg.delete = edit_text, delete
        self.messages.append(msg)
        return msg


@pytest.fixture
def no_db(monkeypatch):
    async def nothing(*a, **k):
        return None

    async def play_id(*a, **k):
        return 1

    async def no(*a, **k):
        return False

    for name in ("get_cached", "set_cached"):
        monkeypatch.setattr(db, name, nothing)
    monkeypatch.setattr(db, "log_play", play_id)
    monkeypatch.setattr(db, "fav_has", no)


def cached_prep(title):
    return {"lazy": title, "cover": "", "cached": (f"id-{title}", title, "A", 200), "d": None}


async def test_telegram_rate_limit_waits_and_sends_the_same_song(no_db):
    bot = FakeBot(retry_first=1)

    async def prepare(uid, item):
        return cached_prep(item["title"])

    failed = await favorites.send_tracks(bot, 1, 1, [{"title": "One"}, {"title": "Two"}], prepare, progress=False)
    assert failed == 0
    assert bot.sent == ["Two", "One"]  # обратный порядок: первая песня окажется внизу и заиграет первой


async def test_stop_ends_sending_between_songs(no_db):
    bot = FakeBot()
    flag = {"stop": False}

    async def prepare(uid, item):
        if item["title"] == "B":
            flag["stop"] = True  # нажали «Стоп», пока готовилась вторая песня
        return cached_prep(item["title"])

    await favorites.send_tracks(
        bot, 1, 1, [{"title": t} for t in "ABC"], prepare, progress=False, stopped=lambda: flag["stop"]
    )
    assert bot.sent == ["C"]


async def test_forwarded_songs_are_collected_into_one_message(monkeypatch):
    monkeypatch.setattr(playlist_bot, "INBOX_QUIET", 0.01)
    monkeypatch.setattr(playlist_bot, "inbox", {})
    bot = FakeBot()

    def forwarded(uid_part, title):
        audio = SimpleNamespace(
            performer="Artist", title=title, file_name=None, duration=200, file_id=f"f-{uid_part}",
            file_unique_id=uid_part, file_size=1000, mime_type="audio/mpeg",
        )  # fmt: skip
        return SimpleNamespace(from_user=SimpleNamespace(id=7), chat=SimpleNamespace(id=7), audio=audio)

    for uid_part, title in [("u1", "One"), ("u2", "Two"), ("u1", "One")]:  # третья — та же песня ещё раз
        m = forwarded(uid_part, title)
        playlist_bot.collect(bot, m, playlist_bot.audio_name(m.audio))
    await asyncio.sleep(0.1)
    assert len(bot.messages) == 1  # одно сообщение на всю пачку
    assert "Received 2 tracks" in bot.messages[0].text


@pytest.mark.parametrize(
    ("performer", "title", "file_name", "expected"),
    [
        ("Queen", "Bohemian Rhapsody", "x.mp3", ("Queen", "Bohemian Rhapsody")),
        (None, None, "Кино - Группа крови.mp3", ("Кино", "Группа крови")),
        (None, None, "track01.mp3", None),
    ],
)
def test_forwarded_song_name_comes_from_tags_or_file_name(performer, title, file_name, expected):
    assert playlist_bot.audio_name(SimpleNamespace(performer=performer, title=title, file_name=file_name)) == expected


async def test_forwarded_song_plays_from_its_telegram_file(monkeypatch):
    """Ключ, под которым сохраняется file_id пересланной песни, — тот же, что ищет избранное перед отправкой."""
    store = {}

    async def set_cached(key, file_id, title, artist, duration):
        store[key] = (file_id, title, artist, duration)

    async def get_cached(key):
        return store.get(key)

    monkeypatch.setattr(db, "set_cached", set_cached)
    monkeypatch.setattr(db, "get_cached", get_cached)
    it = {"artist": "Artist", "title": "One", "duration": 200}
    await db.set_cached(
        f"audio:{search.lazy_for(it['artist'], it['title'], it['duration'])}", "tg-file", "One", "Artist", 200
    )
    fav = {**it, "cover": "", "url": ""}
    prep = await favorites.prepare_favorite(1, fav)
    assert prep["cached"][0] == "tg-file" and prep["d"] is None


async def test_playlist_goes_to_favorites_in_order_and_once(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    await db.init_db()
    items = [
        {"artist": "A", "title": "First", "duration": 1, "url": "https://www.youtube.com/watch?v=1"},
        {"artist": "B", "title": "Second", "duration": 2, "url": ""},
    ]
    assert await db.fav_add_many(5, items) == 2
    assert await db.fav_add_many(5, items) == 0  # повторно ничего не добавляется
    favs = await db.fav_list(5)
    assert [f["title"] for f in favs] == ["First", "Second"]  # как в плейлисте
    assert favs[0]["url"] == "https://www.youtube.com/watch?v=1"


def test_favorites_cache_is_limited_by_size(monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(favcache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(favcache, "MAX_BYTES", 25)
    for i in range(5):
        p = tmp_path / f"{i:032x}.m4a"
        p.write_bytes(b"x" * 10)
        os.utime(p, (1000 + i, 1000 + i))
    favcache._prune()
    assert sorted(p.name[-6:] for p in tmp_path.iterdir()) == ["03.m4a", "04.m4a"]


async def test_messages_are_routed_to_the_playlist_handlers_only_when_they_should_be():
    def text(t):
        return SimpleNamespace(text=t)

    assert await playlist_bot.playlist_link(text("look https://open.spotify.com/album/4yP0hdKOZPNshxUOjY0cZj")) == {
        "found": ("spotify:album", "4yP0hdKOZPNshxUOjY0cZj")
    }
    assert await playlist_bot.playlist_link(text("https://www.youtube.com/watch?v=4NRXx6U8ABQ")) is False
    assert await playlist_bot.playlist_link(text("believer")) is False

    def audio_msg(forwarded, title):
        audio = SimpleNamespace(performer="A", title=title, file_name=None)
        return SimpleNamespace(audio=audio, forward_origin=object() if forwarded else None, via_bot=None)

    assert await playlist_bot.named_forward(audio_msg(True, "Song")) == {"name": ("A", "Song")}
    assert await playlist_bot.named_forward(audio_msg(False, "Song")) is False  # своё аудио — распознавание
    assert await playlist_bot.named_forward(audio_msg(True, None)) is False  # без названия — распознавание


@pytest.mark.parametrize(
    ("title", "core"),
    [
        ("Draw You Out - From The Hunger Games: Sunrise on the Reaping - The Album", "Draw You Out"),
        ("Here Comes The Sun - Remastered 2009", "Here Comes The Sun"),
        ("Bohemian Rhapsody - 2011 Remaster", "Bohemian Rhapsody"),
        ("Blinding Lights - Radio Edit", "Blinding Lights"),
        ("Stateside + Zara Larsson", "Stateside"),
        ("Rein Me In (with Olivia Dean)", "Rein Me In (with Olivia Dean)"),  # скобки сравнение и так пропускает
        ("Hello - Acoustic Version", "Hello - Acoustic Version"),  # другая запись: не трогаем
        ("Love Story (Taylor's Version)", "Love Story (Taylor's Version)"),
        ("Anti-Hero", "Anti-Hero"),
        ("- From", "- From"),  # отрезать нечего — оставляем как было
    ],
)
def test_catalog_tails_are_cut_from_song_names(title, core):
    from musicbot.textutil import core_title

    assert core_title(title) == core


async def test_spotify_name_with_a_tail_finds_the_short_youtube_version(monkeypatch, result):
    from musicbot.search import fetch
    from musicbot.search.models import Outcome

    asked = []

    async def fake_search(query, limit=8, deep=False, fast_only=False):
        asked.append(query)
        return Outcome([result("Draw You Out", "Noah Kahan", 255, url="https://www.youtube.com/watch?v=abc")])

    monkeypatch.setattr(fetch, "search", fake_search)
    meta = result("Draw You Out - From The Hunger Games: Sunrise on the Reaping - The Album", "Noah Kahan", 255)
    found = await fetch.resolve(meta)
    assert [r.url for r in found] == ["https://www.youtube.com/watch?v=abc"]
    assert asked[0] == "Noah Kahan Draw You Out"  # и ищем по короткому названию


async def test_songs_that_failed_are_named_in_playlist_order(no_db):
    bot = FakeBot()

    async def prepare(uid, item):
        if item["title"] in ("B", "D"):
            raise RuntimeError("not found")
        return cached_prep(item["title"])

    missed = []
    failed = await favorites.send_tracks(
        bot, 1, 1, [{"title": t} for t in "ABCD"], prepare, progress=False, failures=missed
    )
    assert failed == 2 and [m["title"] for m in missed] == ["B", "D"]


@pytest.mark.parametrize(
    ("catalog", "youtube", "same"),
    [
        ("Zemfira", "Земфира", True),
        ("Kino", "Кино", True),
        ("Splean", "Сплин", True),  # своё латинское написание группы, близкое к транслиту
        ("Mumiy Troll", "Мумий Тролль", True),
        ("DDT", "ДДТ", True),
        ("Kino", "Хэт", False),
        ("Splean", "GANCAYCO", False),
        ("Lana", "Lena", False),  # короткие имена — только точное совпадение
    ],
)
def test_artist_names_in_another_alphabet_are_the_same_artist(catalog, youtube, same):
    from musicbot.textutil import same_artist

    assert same_artist(catalog, youtube) is same


async def test_latin_artist_from_spotify_finds_the_cyrillic_youtube_upload(monkeypatch, result):
    from musicbot.search import fetch
    from musicbot.search.models import Outcome

    async def fake_search(query, limit=8, deep=False, fast_only=False):
        return Outcome(
            [
                result("Группа крови", "Хэт", 290, url="https://www.youtube.com/watch?v=cover"),  # чужая песня
                result("Группа крови", "Кино", 285, url="https://www.youtube.com/watch?v=kino"),
            ]
        )

    monkeypatch.setattr(fetch, "search", fake_search)
    found = await fetch.resolve(result("Группа крови", "Kino", 284))
    assert [r.url for r in found] == ["https://www.youtube.com/watch?v=kino"]


def test_mix_cut_tail_is_cut_and_recognized():
    from musicbot.textutil import core_title, is_mix_cut, norm

    title = "Monument (feat. Robyn) [True Electric] - Mixed"
    assert core_title(title) == "Monument (feat. Robyn) [True Electric]"
    assert norm(core_title(title)) == norm("Monument [True Electric] (feat. Robyn)")  # как на YouTube Music
    assert is_mix_cut(title) and is_mix_cut("Song - Mix Cut")
    assert not is_mix_cut("Mixed Emotions") and not is_mix_cut("Song - Remix")


async def test_mix_cut_from_spotify_is_found_even_if_its_length_differs(monkeypatch, result, tmp_path):
    """Длина «- Mixed» в каталоге — длина куска в миксе: на YouTube та же запись длиннее."""
    from musicbot.download import Downloaded
    from musicbot.search import fetch
    from musicbot.search.models import Outcome

    async def fake_search(query, limit=8, deep=False, fast_only=False):
        return Outcome(
            [result("Monument [True Electric] (feat. Robyn)", "Röyksopp", 415, url="https://www.youtube.com/watch?v=m")]
        )

    asked = []

    async def fake_download(url, kind, meta=None, fallbacks=True):
        asked.append(url)
        p = tmp_path / "m.m4a"
        p.write_bytes(b"x")
        return Downloaded(path=p, thumb=None, title="x", artist="y", duration=415, source="Youtube", workdir=tmp_path)

    monkeypatch.setattr(fetch, "search", fake_search)
    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 415)
    monkeypatch.setattr(fetch, "DOWNLOAD_DIR", tmp_path / "dl")
    meta = result("Monument (feat. Robyn) [True Electric] - Mixed", "Röyksopp, Robyn", 380, url="lazy://m")
    d = await fetch.fetch_audio("lazy://m", meta)
    assert asked == ["https://www.youtube.com/watch?v=m"]
    assert d.title == "Monument (feat. Robyn) [True Electric] - Mixed"  # имя — как в плейлисте


async def test_song_with_guests_is_searched_by_the_main_artist_first(monkeypatch, result):
    """С полным списком исполнителей YouTube Music не показывает альбомную версию «Clint Eastwood»."""
    from musicbot.search import fetch
    from musicbot.search.models import Outcome

    asked = []

    async def fake_search(query, limit=8, deep=False, fast_only=False):
        asked.append(query)
        if query == "Gorillaz Clint Eastwood":
            return Outcome([result("Clint Eastwood", "Gorillaz, Del The Funky Homosapien", 341, url="https://y/album")])
        return Outcome([result("Clint Eastwood", "Gorillaz", 267, url="https://y/edit")])  # радиоверсия короче

    monkeypatch.setattr(fetch, "search", fake_search)
    found = await fetch.resolve(result("Clint Eastwood", "Gorillaz, Del The Funky Homosapien", 340))
    assert [r.url for r in found] == ["https://y/album"]
    assert asked[0] == "Gorillaz Clint Eastwood"


async def test_full_artist_list_is_tried_when_the_main_artist_finds_nothing(monkeypatch, result):
    from musicbot.search import fetch
    from musicbot.search.models import Outcome

    asked = []

    async def fake_search(query, limit=8, deep=False, fast_only=False):
        asked.append(query)
        if "Robyn" in query:
            return Outcome([result("Monument", "Röyksopp & Robyn", 415, url="https://y/m")])
        return Outcome([result("Other", "Röyksopp", 300, url="https://y/o")])

    monkeypatch.setattr(fetch, "search", fake_search)
    found = await fetch.resolve(result("Monument", "Röyksopp, Robyn", 415))
    assert [r.url for r in found] == ["https://y/m"]
    assert asked == ["Röyksopp Monument", "Röyksopp, Robyn Monument"]
