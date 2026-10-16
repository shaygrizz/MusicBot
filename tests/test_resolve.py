"""Подбор версии песни по названию: находит нужную запись и не берёт чужую."""

import pytest

from musicbot import favorites
from musicbot.download import Downloaded
from musicbot.search import fetch
from musicbot.search.models import Outcome


def searcher(monkeypatch, answers: dict, default=()):
    """Подменяет поиск: ответ по точному тексту запроса, иначе default. Возвращает список заданных запросов."""
    asked = []

    async def fake_search(query, limit=8, deep=False, fast_only=False, fresh=False):
        asked.append((query, fast_only))
        return Outcome(list(answers.get(query, default)))

    monkeypatch.setattr(fetch, "search", fake_search)
    return asked


@pytest.mark.parametrize(
    ("want", "found", "reason"),
    [
        # «the» из «Del The Funky Homosapien» не делает чужого исполнителя своим
        (
            ("Clint Eastwood", "Gorillaz, Del The Funky Homosapien", 340),
            ("Clint Eastwood", "The Killers", 340),
            "исполнитель",
        ),
        # кавер с тем же названием и почти той же длиной
        (("Группа крови", "Kino", 285), ("Группа крови", "Хэт", 290), "исполнитель"),
        (("Art Deco", "Lana Del Rey", 295), ("Art Deco (sped up)", "Lana Del Rey", 296), "ремикс"),
        (("Art Deco", "Lana Del Rey", 295), ("Art Deco (Parov Stelar Artifact Version)", "Lana Del Rey", 199), "длина"),
        (("One", "U2", 276), ("One", "The U2 Tribute Band", 276), "исполнитель"),  # короткое имя — только точно
        (("Believer", "Imagine Dragons", 204), ("Thunder", "Imagine Dragons", 204), "название"),
    ],
)
def test_other_songs_are_refused(result, want, found, reason):
    assert reason in fetch.reject_reason(result(*want), result(*found))


@pytest.mark.parametrize(
    ("want", "found"),
    [
        (("Clint Eastwood", "Gorillaz, Del The Funky Homosapien", 340), ("Clint Eastwood", "Gorillaz", 341)),
        (("Группа крови", "Kino", 285), ("Группа крови", "Кино", 285)),
        (
            ("Manchild", "Sabrina Carpenter", 213),
            ("Sabrina Carpenter - Manchild (Official Audio)", "Kaguras Music", 215),
        ),
        (("Monument", "Röyksopp, Robyn", 415), ("Monument", "Röyksopp & Robyn", 418)),
        (("Gruppa krovi", "Kino", 285), ("Группа крови", "Кино", 285)),  # название латиницей
        (("One", "U2", 276), ("One", "U2", 276)),
    ],
)
def test_the_same_song_is_accepted(result, want, found):
    assert fetch.reject_reason(result(*want), result(*found)) is None


async def test_title_only_query_finds_an_artist_spelled_differently(monkeypatch, result):
    hit = result("Выхода нет", "Сплин", 231, url="https://y/splin")
    asked = searcher(monkeypatch, {"Выхода нет": [hit]})
    found = await fetch.resolve(result("Выхода нет", "Splean", 231))
    assert [r.url for r in found] == ["https://y/splin"]
    assert [q for q, _ in asked] == ["Splean Выхода нет", "Выхода нет"]


async def test_radio_edit_vs_album_length_is_accepted_only_as_a_last_resort(monkeypatch, result):
    near = result("Clint Eastwood", "Gorillaz", 320, url="https://y/near")  # на 20 с короче
    far = result("Clint Eastwood", "Gorillaz", 267, url="https://y/edit")  # на 73 с — другая версия
    asked = searcher(monkeypatch, {}, default=[near, far])
    found = await fetch.resolve(result("Clint Eastwood", "Gorillaz", 340))
    assert [(r.url, r.loose) for r in found] == [("https://y/near", True)]
    assert asked[-1] == ("Gorillaz Clint Eastwood", False)  # перед мягкой проверкой был и полный поиск


async def test_loose_version_is_checked_against_its_own_length(monkeypatch, result, tmp_path):
    near = result("Clint Eastwood", "Gorillaz", 320, url="https://y/near")
    searcher(monkeypatch, {}, default=[near])

    async def fake_download(url, kind, meta=None, fallbacks=True):
        p = tmp_path / "a.m4a"
        p.write_bytes(b"x")
        return Downloaded(path=p, thumb=None, title="x", artist="y", duration=320, source="Youtube", workdir=tmp_path)

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(fetch, "probe_duration", lambda path: 321)  # файл как у версии, а не как в каталоге (340)
    monkeypatch.setattr(fetch, "DOWNLOAD_DIR", tmp_path / "dl")
    d = await fetch.fetch_audio("lazy://g", result("Clint Eastwood", "Gorillaz", 340, url="lazy://g"))
    assert d.origin == "https://y/near" and d.title == "Clint Eastwood"


async def test_a_miss_names_what_was_found_and_why(monkeypatch, result):
    searcher(monkeypatch, {}, default=[result("Clint Eastwood", "The Killers", 341), result("Other", "X", 10)])
    why = []
    assert await fetch.resolve(result("Clint Eastwood", "Gorillaz", 340), why=why) == []
    assert why and why[0][1] == "другой исполнитель"  # первым — то, что ближе всего (название совпало)


async def test_song_waits_out_a_youtube_block_and_is_tried_again(monkeypatch):
    calls = []

    async def fake_fetch(url, meta):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("YouTube временно пропущен (недавняя блокировка IP)")
        return "file"

    async def no_cache(item):
        return None

    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(favorites, "_cached", no_cache)
    monkeypatch.setattr(favorites.search, "fetch_audio", fake_fetch)
    monkeypatch.setattr(favorites.download, "youtube_block_left", lambda: 100.0)
    monkeypatch.setattr(favorites.asyncio, "sleep", fake_sleep)
    prep = await favorites.prepare_track(1, {"artist": "A", "title": "T", "duration": 200, "url": ""})
    assert prep["d"] == "file" and len(calls) == 2 and slept == [102.0]


async def test_failure_without_a_block_is_not_retried(monkeypatch):
    async def fake_fetch(url, meta):
        raise RuntimeError("not found")

    async def no_cache(item):
        return None

    monkeypatch.setattr(favorites, "_cached", no_cache)
    monkeypatch.setattr(favorites.search, "fetch_audio", fake_fetch)
    monkeypatch.setattr(favorites.download, "youtube_block_left", lambda: 0.0)
    with pytest.raises(RuntimeError):
        await favorites.prepare_track(1, {"artist": "A", "title": "T", "duration": 200, "url": ""})


async def test_check_playlist_prints_misses_with_reasons(monkeypatch, capsys, result):
    from musicbot import check_playlist, playlists

    pl = playlists.Playlist(
        "Mix",
        "spotify",
        [
            playlists.Track("Gorillaz", "Clint Eastwood", 340),
            playlists.Track("Nobody", "Nothing", 100),
            playlists.Track("A", "Linked", 100, url="https://www.youtube.com/watch?v=x"),
        ],
    )

    async def nothing():
        return None

    monkeypatch.setattr(check_playlist.db, "init_db", nothing)
    monkeypatch.setattr(check_playlist.playlists, "read", lambda kind, ident: pl)
    searcher(monkeypatch, {"Gorillaz Clint Eastwood": [result("Clint Eastwood", "Gorillaz", 341, url="https://y/g")]})
    code = await check_playlist.check("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    out = capsys.readouterr().out
    assert code == 0
    assert "✗ 2. Nobody - Nothing" in out and "Clint Eastwood" not in out
    assert "Найдено 2 из 3" in out


async def test_partial_answer_is_asked_again_past_the_cache(monkeypatch, result):
    """YouTube Music не успел ответить: выдача неполная и лежит в кэше. Повторяем мимо кэша, а не сдаёмся."""
    hit = result("Too Late", "The Weeknd", 241, url="https://y/late")
    asked = []

    async def fake_search(query, limit=8, deep=False, fast_only=False, fresh=False):
        asked.append((query, fast_only, fresh))
        if fresh:
            return Outcome([hit])
        return Outcome([], partial=True)

    monkeypatch.setattr(fetch, "search", fake_search)
    found = await fetch.resolve(result("Too Late", "The Weeknd", 239))
    assert [r.url for r in found] == ["https://y/late"]
    assert ("The Weeknd Too Late", True, True) in asked
