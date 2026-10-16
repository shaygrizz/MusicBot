import pytest

from musicbot.search.exact import core_title, exact_refs, is_clean_canon, is_exact, main_artist, mark_exact
from musicbot.search.models import Canon


def test_official_audio_with_same_length_is_exact(result, weeknd):
    assert is_exact(result("Blinding Lights", "The Weeknd", 202), weeknd)
    assert is_exact(result("Blinding Lights (Official Audio)", "The Weeknd", 200), weeknd)


@pytest.mark.parametrize(
    "title",
    [
        "Blinding Lights (Remix)",
        "Blinding Lights (Live)",
        "Blinding Lights (Piano Version)",
        "Blinding Lights (Slowed)",
    ],
)
def test_altered_versions_are_not_exact(result, weeknd, title):
    assert not is_exact(result(title, "The Weeknd", 200), weeknd)


def test_lookalike_artist_is_not_exact(result, weeknd):
    assert not is_exact(result("Blinding Lights", "The Retro Weeknd", 200), weeknd)


def test_wrong_length_is_not_exact(result, weeknd):
    assert not is_exact(result("Blinding Lights", "The Weeknd", 263), weeknd)


def test_unknown_length_counts_only_for_official_sources(result, weeknd):
    assert is_exact(result("Blinding Lights", "The Weeknd", 0, source="ytm"), weeknd)
    assert not is_exact(result("Blinding Lights", "The Weeknd", 0, source="ytmv"), weeknd)


def test_artist_prefix_in_title_counts_as_artist(result, weeknd):
    assert is_exact(result("The Weeknd - Blinding Lights (Lyrics)", "Some Channel", 200), weeknd)


def test_core_title_strips_artist_feat_and_collects_qualifiers():
    core, quals = core_title("The Weeknd - Blinding Lights (feat. X) [Official Video]", "The Weeknd")
    assert core == "blindinglights"
    assert quals == ["feat. X", "Official Video"]


def test_main_artist_is_the_first_of_several():
    assert main_artist("The Weeknd, Anitta") == "The Weeknd"
    assert main_artist("Tame Impala & Blood Orange") == "Tame Impala"


@pytest.mark.parametrize(
    ("title", "clean"),
    [("Song", True), ("Song (feat. B)", True), ("Song (Live)", False), ("Song (Single Version)", False)],
)
def test_clean_canon(title, clean):
    assert is_clean_canon(Canon("A", title, 200)) is clean


def test_only_most_popular_song_with_that_title_is_reference():
    canons = [
        Canon("Imagine Dragons", "Believer", 204),
        Canon("Believer", "Everytime You", 227),
        Canon("Kamelot", "Believer", 250),
    ]
    refs = exact_refs(canons, "believer")
    assert [c.artist for c in refs] == ["Imagine Dragons"]


def test_artist_named_like_the_query_is_not_a_named_artist():
    canons = [Canon("The Weeknd", "Starboy", 230), Canon("Starboy", "Everytime You", 227)]
    assert exact_refs(canons, "starboy")[0].artist == "The Weeknd"


def test_variant_of_another_length_is_not_a_reference():
    canons = [Canon("Tame Impala", "Borderline", 238), Canon("Tame Impala", "Borderline", 438)]
    assert [c.duration for c in exact_refs(canons, "tame impala borderline")] == [238]


def test_mark_exact_fills_unknown_duration_from_catalog(result, weeknd):
    r = result("Blinding Lights", "The Weeknd", 0, source="ytm")
    mark_exact(r, [weeknd])
    assert r.exact and r.duration == 200
