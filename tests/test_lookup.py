import pytest

from posteryard import lookup
from tests.fakes import LookupPlex

LIBRARIES = ("Movies", "TV Shows")


def resolve(text: str) -> lookup.Match:
    return lookup.resolve(LookupPlex(), LIBRARIES, text)


@pytest.mark.parametrize(
    "text", ["The Office", "the office", "THE OFFICE ", "The Office 2005", "The Office (2005)", "5646"]
)
def test_names_years_and_keys_find_the_title(text: str) -> None:
    assert resolve(text).rating_key == "5646"


def test_punctuation_is_ignored() -> None:
    assert resolve("thunderbolts").rating_key == "302"


def test_a_year_in_the_title_is_not_read_as_a_year() -> None:
    assert resolve("Blade Runner 2049").rating_key == "301"


def test_same_name_lists_every_match_and_how_to_pick_one() -> None:
    with pytest.raises(lookup.TitleError) as caught:
        resolve("Dune")
    assert [m.rating_key for m in caught.value.matches] == ["4411", "8120"]
    text = caught.value.explain("art next")
    assert 'posteryard art next "Dune 1984"   or 4411' in text
    assert text.endswith("Nothing changed.")
    assert resolve("Dune 2021").rating_key == "8120"


def test_a_number_that_is_also_a_title_is_ambiguous() -> None:
    with pytest.raises(lookup.TitleError) as caught:
        resolve("1917")
    assert {m.rating_key for m in caught.value.matches} == {"1917", "300"}
    assert resolve("1917 2019").rating_key == "300"


def test_a_typo_suggests_close_titles_and_changes_nothing() -> None:
    with pytest.raises(lookup.TitleError) as caught:
        resolve("The Ofice")
    assert not caught.value.ambiguous
    assert [m.rating_key for m in caught.value.matches] == ["5646"]
    assert "Did you mean" in caught.value.explain("art next")


def test_nothing_close_points_to_find() -> None:
    with pytest.raises(lookup.TitleError) as caught:
        resolve("Zzyzx")
    assert caught.value.matches == []
    assert "posteryard find" in caught.value.explain("art next")


def test_search_finds_parts_of_names() -> None:
    titles = lookup.library_titles(LookupPlex(), LIBRARIES)
    assert [m.rating_key for m in lookup.search(titles, "dune")] == ["4411", "8120"]
    assert lookup.search(titles, "  ") == []


def test_season_picks_a_child_or_explains() -> None:
    plex = LookupPlex()
    show = resolve("The Office")
    season = lookup.season(plex, show, 2)
    assert season.rating_key == "5648"
    assert season.label == "The Office (2005) Season 2"
    with pytest.raises(ValueError, match="Seasons in Plex: 1, 2"):
        lookup.season(plex, show, 9)
    plex.name = "Jellyfin"
    with pytest.raises(ValueError, match="Seasons in Jellyfin: 1, 2"):
        lookup.season(plex, show, 9)
    with pytest.raises(ValueError, match="not a show"):
        lookup.season(plex, resolve("Dune 2021"), 1)
