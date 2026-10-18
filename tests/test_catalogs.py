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


def test_famous_artist_is_found_even_if_her_song_is_not_the_first_track(monkeypatch):
    # с другого адреса (сервер в Польше) первым трек может оказаться чужим — AURORA всё равно должна найтись
    other = track("Aurora Borealis", "DJ Someone", rank=300_000)
    other["artist"]["id"] = 5
    runaway = track("Runaway", "AURORA", rank=945_380)
    runaway["artist"]["id"] = 7
    fake_deezer(
        monkeypatch,
        artists=[{"id": 1, "name": "Aurora", "nb_fan": 2_484}],
        tracks=[other, runaway],
        top=[track("Runaway", "AURORA"), track("The Seed", "AURORA")],
        info={7: {"id": 7, "name": "AURORA", "nb_fan": 1_350_000}},
    )
    name, songs = deezer_artist("aurora")
    assert name == "AURORA"
    assert [c.title for c in songs] == ["Runaway", "The Seed"]


def song_by(title, artist, artist_id, **kw):
    t = track(title, artist, **kw)
    t["artist"]["id"] = artist_id
    return t


BEATLES = [{"id": 1, "name": "Beat-les", "nb_fan": 22}, {"id": 2, "name": "The Beatles", "nb_fan": 8_148_155}]


def test_artist_is_found_without_the_article(monkeypatch):
    # «beatles» совпадало только с безвестным «Beat-les»: «The Beatles» отсекалось нечётким сравнением из-за «the»
    fake_deezer(
        monkeypatch,
        artists=BEATLES,
        tracks=[song_by("Let It Be", "The Beatles", 2)],
        top=[track("Hey Jude", "The Beatles")],
    )
    name, songs = deezer_artist("beatles")
    assert name == "The Beatles"
    assert songs[0].title == "Hey Jude"


@pytest.mark.parametrize("query", ["the beatles", "beatls", "the beatls"])
def test_artist_with_the_or_a_typo_is_found_when_deezer_puts_it_first(monkeypatch, query):
    fake_deezer(
        monkeypatch,
        artists=BEATLES[::-1],
        tracks=[song_by("Black Beatles", "Rae Sremmurd", 8)],
        top=[track("Hey Jude", "The Beatles")],
    )
    assert deezer_artist(query)[0] == "The Beatles"


def test_famous_artist_wins_over_a_small_namesake_without_the(monkeypatch):
    fake_deezer(
        monkeypatch,
        artists=[{"id": 1, "name": "Weeknd", "nb_fan": 2_100}, {"id": 2, "name": "The Weeknd", "nb_fan": 14_745_145}],
        tracks=[song_by("Blinding Lights", "The Weeknd", 2)],
        top=[track("Starboy", "The Weeknd")],
    )
    assert deezer_artist("weeknd")[0] == "The Weeknd"


@pytest.mark.parametrize("query", ["animals", "police", "door", "killer"])
def test_single_word_song_title_does_not_open_a_the_band(monkeypatch, query):
    # у «The Animals» / «The Police» / «The Doors» песни не в первых треках по запросу: ищут одноимённую песню
    band = {"The Animals": 1, "The Police": 2, "The Doors": 3, "The Killers": 4}
    name = {"animals": "The Animals", "police": "The Police", "door": "The Doors", "killer": "The Killers"}[query]
    fake_deezer(
        monkeypatch,
        artists=[{"id": 99, "name": query.title(), "nb_fan": 40}, {"id": band[name], "name": name, "nb_fan": 900_000}],
        tracks=[song_by(query.title(), "Someone Else", 77)],
        top=[track("Hit", name)],
    )
    assert deezer_artist(query) is None


@pytest.mark.parametrize(
    ("query", "catalog_name"),
    [
        ("a ha", "a-ha"),
        ("simon and garfunkel", "Simon & Garfunkel"),
        ("acdc", "AC/DC"),
        ("guns n’ roses", "Guns N' Roses"),
    ],
)
def test_spacing_and_and_do_not_hide_the_artist(monkeypatch, query, catalog_name):
    fake_deezer(
        monkeypatch,
        artists=[{"id": 1, "name": catalog_name, "nb_fan": 1_178_956}],
        tracks=[],
        top=[track("Take On Me", catalog_name)],
    )
    assert deezer_artist(query)[0] == catalog_name


def test_song_with_an_article_stays_a_song(monkeypatch):
    # «the scientist» — песня Coldplay; безвестный «Scientist» после отбрасывания «the» не должен её перекрыть
    fake_deezer(
        monkeypatch,
        artists=[{"id": 1, "name": "The Scientist", "nb_fan": 3_794}, {"id": 2, "name": "Scientist", "nb_fan": 4_438}],
        tracks=[track("The Scientist", "Coldplay", rank=950_000)],
        top=[track("Dub", "Scientist")],
    )
    assert deezer_artist("the scientist") is None
