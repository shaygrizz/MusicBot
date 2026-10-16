from musicbot.search.models import Canon
from musicbot.search.ranking import PRIORITY, merge, rank, same_track, verify


def test_exact_original_comes_first_and_gets_catalog_name(result, weeknd):
    pool = [
        result("Blinding Lights (Piano Version)", "Piano Peace", 240),
        result("Blinding Lights (Remix)", "Some DJ", 241, source="au"),
        result("blinding lights", "the weeknd", 0, source="ytm"),
    ]
    out = merge(pool, [weeknd], "the weeknd blinding lights", 8)
    assert out[0].exact
    assert (out[0].title, out[0].artist) == ("Blinding Lights", "The Weeknd")
    assert out[0].duration == 200


def test_exact_tier_beats_verified_but_altered_versions(result):
    exact = result("Song", "Artist", 200, source="ytmv")
    exact.exact = True
    other = result("Song", "Artist", 200, source="ytm")
    assert rank(exact, "song") < rank(other, "song")


def test_remixes_rank_last_unless_asked(result):
    remix = result("Song (Remix)", "DJ", 200)
    assert rank(remix, "song") == PRIORITY["bad"] + 10
    assert rank(remix, "song remix") < PRIORITY["bad"] + 10


def test_duplicates_with_same_title_and_length_are_collapsed(result):
    a = result("Song", "Artist", 200, url="u1")
    b = result("Song", "Artist", 202, url="u2")
    c = result("Song", "Artist", 260, url="u3")
    assert same_track(a, b)
    assert not same_track(a, c)


def test_verify_requires_catalog_title_and_artist(result, weeknd):
    assert verify(result("Blinding Lights", "The Weeknd", 201), [weeknd]) is weeknd
    assert verify(result("Blinding Lights", "Somebody Else", 201), [weeknd]) is None


def test_audius_remixes_keep_reserved_places_at_the_end(result):
    pool = [result(f"Blinding Lights v{i}", "The Weeknd", 200 + 10 * i, url=f"u{i}") for i in range(6)]
    pool.append(result("Blinding Lights (Remix)", "DJ", 241, source="au", url="au1"))
    out = merge(pool, [Canon("The Weeknd", "Blinding Lights", 200)], "blinding lights", 4, strict=False)
    assert len(out) == 4
    assert out[-1].source == "au"
