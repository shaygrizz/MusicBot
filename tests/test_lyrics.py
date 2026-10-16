import pytest

from musicbot.search import lyrics
from musicbot.search.lyrics import clean_genius_title, lyric_candidates, respell, sings_it


def hit(*lines):
    return {"highlights": [{"value": "\n".join(lines)}]}


def test_translation_in_brackets_is_dropped_for_cyrillic_titles():
    assert clean_genius_title("Мама, я танцую (Mom, I Dance)") == "Мама, я танцую"
    assert clean_genius_title("Hello (Remastered)") == "Hello (Remastered)"


def test_song_that_sings_the_line_is_accepted():
    h = hit("Is this the real life? Is this just fantasy?", "Caught in a landslide")
    assert sings_it(h, "is this the real life is this just fantasy")


def test_song_that_only_mentions_the_title_is_rejected():
    h = hit("Ik voel me tame impala the less I know the better")
    assert not sings_it(h, "tame impala the less i know the better")


def test_typos_need_fuzzy_mode():
    h = hit("Is this the real life? Is this just fantasy?")
    q = "is this the reel life is this just fantazy"
    assert not sings_it(h, q)
    assert sings_it(h, q, fuzzy=True)


def test_short_queries_never_match():
    assert not sings_it(hit("la la la"), "la")


def test_respell_uses_words_of_found_titles():
    fixed = respell("cause baby your a fireworc", ["Katy Perry Firework", "Baby You're A Firework"])
    assert "firework" in fixed
    assert fixed.startswith("cause baby")


def test_respell_keeps_unknown_words_and_known_ones():
    assert respell("hello zzzzzz", ["Hello World"]) == "hello zzzzzz"


@pytest.fixture
def fake_apis(monkeypatch):
    line = "Is this the real life? Is this just fantasy?"
    genius = {
        "response": {
            "sections": [
                {
                    "hits": [
                        {
                            "highlights": [{"value": line}],
                            "result": {"title": "New Music Friday", "primary_artist": {"name": "Spotify"}},
                        },
                        {
                            "highlights": [{"value": line}],
                            "result": {"title": "Bohemian Rhapsody", "primary_artist": {"name": "Queen"}},
                        },
                    ]
                }
            ]
        }
    }
    deezer = {
        "data": [
            {
                "artist": {"name": "Queen"},
                "title": "Bohemian Rhapsody",
                "duration": 354,
                "rank": 958949,
                "album": {"cover_big": "c.jpg"},
            }
        ]
    }

    def fake_get_json(url, *args, **kwargs):
        return genius if "genius.com" in url else deezer

    monkeypatch.setattr(lyrics, "get_json", fake_get_json)


def test_only_songs_found_in_deezer_are_returned(fake_apis):
    found = lyric_candidates("is this the real life is this just fantasy")
    assert [(c.artist, c.title, c.duration) for c in found] == [("Queen", "Bohemian Rhapsody", 354)]
