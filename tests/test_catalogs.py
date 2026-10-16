import pytest

from musicbot.search import catalogs
from musicbot.search.catalogs import canon_relevant, dedupe_canons, deezer_artist
from musicbot.search.models import Canon
from musicbot.search.text import clean_query, query_tokens


def test_typo_in_a_title_word_does_not_hide_the_catalog_entry():
    c = Canon("The Weeknd", "São Paulo", 302)
    assert canon_relevant(c, query_tokens("sao paolo the weeknd"))


def test_other_song_of_the_same_artist_is_not_relevant():
    c = Canon("The Weeknd", "Starboy", 230)
    assert not canon_relevant(c, query_tokens("sao paolo the weeknd"))


def test_query_noise_words_are_removed():
    assert clean_query("скачать Believer mp3") == "Believer"


def test_dedupe_keeps_first_of_equal_title_and_artist():
    a = Canon("Queen", "Bohemian Rhapsody", 354)
    b = Canon("queen", "bohemian rhapsody", 355)
    radio = Canon("Queen", "Radio Ga Ga", 348)
    assert dedupe_canons([[a], [b, radio]]) == [a, radio]


def track(title, artist, rank=900_000, duration=200):
    return {
        "title": title,
        "artist": {"name": artist},
        "contributors": [{"name": artist}],
        "duration": duration,
        "rank": rank,
        "album": {"cover_big": ""},
    }


def fake_deezer(monkeypatch, artists, tracks, top, info=None):
    def get_json(url, *args, **kwargs):
        if "/search/artist" in url:
            return {"data": artists}
        if "/artist/" in url and "/top" in url:
            return {"data": top}
        if "/artist/" in url:
            return (info or {}).get(int(url.rsplit("/", 1)[1]), {})
        return {"data": tracks}

    monkeypatch.setattr(catalogs, "get_json", get_json)


def test_artist_with_a_typo_in_the_name_is_found(monkeypatch):
    fake_deezer(
        monkeypatch,
        artists=[
            {"id": 1, "name": "Kendrick Lamar", "nb_fan": 6_186_502},
            {"id": 2, "name": "Kendrick Lama", "nb_fan": 105},
        ],
        tracks=[track("HUMBLE.", "Kendrick Lamar")],
        top=[track("Not Like Us", "Kendrick Lamar"), track("HUMBLE.", "Kendrick Lamar")],
    )
    name, songs = deezer_artist("kendric lamar")
    assert name == "Kendrick Lamar"
    assert [c.title for c in songs] == ["Not Like Us", "HUMBLE."]


def test_obscure_lookalike_artist_is_not_accepted(monkeypatch):
    fake_deezer(monkeypatch, artists=[{"id": 2, "name": "Kendrick Lama", "nb_fan": 105}], tracks=[], top=[])
    assert deezer_artist("kendric lamar") is None


def test_famous_song_wins_over_obscure_band_with_the_same_name(monkeypatch):
    fake_deezer(
        monkeypatch,
        artists=[{"id": 9, "name": "Believer", "nb_fan": 7_663}],
        tracks=[track("Believer", "Imagine Dragons", rank=946_141)],
        top=[track("No Apology", "Believer")],
    )
    assert deezer_artist("believer") is None


def test_famous_artist_stays_an_artist_even_if_a_song_shares_the_name(monkeypatch):
    fake_deezer(
        monkeypatch,
        artists=[{"id": 3, "name": "Queen", "nb_fan": 12_834_776}],
        tracks=[track("Queen", "Someone Else", rank=950_000)],
        top=[track("Bohemian Rhapsody", "Queen")],
    )
    name, songs = deezer_artist("queen")
    assert name == "Queen"
    assert songs[0].title == "Bohemian Rhapsody"


@pytest.mark.parametrize("query", ["x", " "])
def test_too_short_queries_are_not_artists(query):
    assert deezer_artist(query) is None


def test_famous_artist_missing_from_artist_search_is_found_by_her_top_song(monkeypatch):
    # по «aurora» Deezer показывает только безвестных Aurora, но первый трек выдачи — AURORA - Runaway
    runaway = track("Runaway", "AURORA", rank=945_380)
    runaway["artist"]["id"] = 7
    fake_deezer(
        monkeypatch,
        artists=[{"id": 1, "name": "Aurora", "nb_fan": 2_484}, {"id": 2, "name": "Aurora", "nb_fan": 389}],
        tracks=[runaway],
        top=[track("Runaway", "AURORA"), track("Cure For Me", "AURORA")],
        info={7: {"id": 7, "name": "AURORA", "nb_fan": 1_350_000}},
    )
    name, songs = deezer_artist("aurora")
    assert name == "AURORA"
    assert [c.title for c in songs] == ["Runaway", "Cure For Me"]
