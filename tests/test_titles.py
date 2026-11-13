import pytest

from posteryard.titles import plain, slug


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Amélie", "AMELIE"),
        ("Straße", "STRASSE"),
        ("Küçük Gelin", "kucuk gelin"),
        ("Spider-Man: No Way Home", "spidermannowayhome"),
    ],
)
def test_titles_that_differ_only_in_case_accents_or_marks_are_plain_equal(first: str, second: str) -> None:
    assert plain(first) == plain(second)


def test_different_titles_stay_different() -> None:
    assert plain("Dune") != plain("Dunes")
    assert plain("1984") == "1984"


def test_slug_keeps_letters_and_digits_up_to_the_length() -> None:
    assert slug("The Office (US): Season 2", 60) == "the-office-us-season-2"
    assert slug("A Very Long Title", 6) == "a-very"
    assert slug("!!!", 10) == ""
