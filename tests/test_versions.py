import importlib

vmod = importlib.import_module("musicbot.search.versions")  # пакет search подменяет имя функцией versions


def patch_sources(monkeypatch, pool):
    def ytm(query, *args):
        return pool

    def none(*args):
        return []

    monkeypatch.setattr(vmod, "search_ytm_top", none)
    monkeypatch.setattr(vmod, "search_ytm", ytm)
    monkeypatch.setattr(vmod, "search_audius", none)
    monkeypatch.setattr(vmod, "search_soundcloud", none)
    monkeypatch.setattr(vmod, "deezer", none)


def pool_of(result, title, artist, names):
    return [result(f"{title} {n}".strip(), artist, 180 + i * 7) for i, n in enumerate(names)]


async def test_original_first_then_popular_kinds_and_cover_last(monkeypatch, result):
    pool = pool_of(
        result, "Blinding Lights", "The Weeknd", ["(Cover)", "(Remix)", "(Slowed + Reverb)", "", "(Sped Up)"]
    )
    patch_sources(monkeypatch, pool)
    out = await vmod.versions("The Weeknd", "Blinding Lights", limit=12)
    labels = [r.label for r in out.results]
    assert labels == ["Original", "Slowed", "Sped up", "Remix", "Cover"]


async def test_words_of_the_song_title_are_not_a_version_mark(monkeypatch, result):
    pool = pool_of(result, "Live Forever", "Oasis", ["", "(Live)"])
    patch_sources(monkeypatch, pool)
    out = await vmod.versions("Oasis", "Live Forever")
    assert [r.label for r in out.results] == ["Original", "Live"]


async def test_limit_cuts_the_least_popular_kinds(monkeypatch, result):
    pool = pool_of(result, "Song", "Artist", ["", "(Cover)", "(Remix)", "(Slowed)", "(Acoustic)"])
    patch_sources(monkeypatch, pool)
    out = await vmod.versions("Artist", "Song", limit=3)
    assert [r.label for r in out.results] == ["Original", "Slowed", "Remix"]


async def test_full_mode_knows_extra_kinds(monkeypatch, result):
    pool = pool_of(result, "Song", "Artist", ["", "(8D Audio)", "(Extended Mix)", "(Lyrics)"])
    patch_sources(monkeypatch, pool)
    short = await vmod.versions("Artist", "Song")
    assert [r.label for r in short.results].count("Original") == len(short.results)  # быстрый режим этих видов не знает
    full = await vmod.versions("Artist", "Song", limit=40, per_kind=6, full=True)
    assert [r.label for r in full.results] == ["Original", "Lyrics", "8D", "Extended"]


async def test_radio_edit_is_short_and_sped_up_reverb_is_sped_up(monkeypatch, result):
    pool = pool_of(result, "Song", "Artist", ["", "(Radio Edit)", "(Sped Up + Reverb)", "(Slowed + Reverb)"])
    patch_sources(monkeypatch, pool)
    out = await vmod.versions("Artist", "Song", limit=40, per_kind=6, full=True)
    assert {r.title: r.label for r in out.results} == {
        "Song": "Original",
        "Song (Radio Edit)": "Short",
        "Song (Sped Up + Reverb)": "Sped up",
        "Song (Slowed + Reverb)": "Slowed",
    }


async def test_artist_name_is_not_a_version_mark(monkeypatch, result):
    pool = [result("Mono Song", "Mono", 180), result("Mono Song (Slowed)", "Mono", 190)]
    patch_sources(monkeypatch, pool)
    out = await vmod.versions("Mono", "Song", full=True)
    assert [r.label for r in out.results] == ["Original", "Slowed"]
