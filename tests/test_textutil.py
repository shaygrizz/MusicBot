import pytest

from musicbot.textutil import BAD_VERSION_RE, fold, norm


def test_fold_removes_latin_diacritics():
    assert fold("São Paulo") == "Sao Paulo"


def test_fold_keeps_cyrillic_letters_intact():
    assert fold("Йёжик") == "Йёжик"


def test_norm_ignores_case_accents_brackets_and_punctuation():
    assert norm("São Paulo (Live)") == "saopaulo"
    assert norm("Sao  Paulo!") == "saopaulo"


@pytest.mark.parametrize(
    "title",
    [
        "Song (Remix)",
        "Song - Live at Wembley",
        "Song (Piano Version)",
        "Song (Acapella)",
        "Song slowed + reverb",
        "Song (Sped Up)",
        "Song Lullaby",
        "Песня кавер",
        "Песня ремикс",
    ],
)
def test_bad_version_detected(title):
    assert BAD_VERSION_RE.search(title)


@pytest.mark.parametrize("title", ["Believer", "Bohemian Rhapsody", "Imagine (Remastered 2010)", "Alive", "Piano Man"])
def test_original_titles_are_not_flagged(title):
    assert not BAD_VERSION_RE.search(title)
