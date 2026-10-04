from datetime import date, timedelta

from posteryard import status
from posteryard.render.layers import APPLE_BLUE, APPLE_GREEN, APPLE_RED, APPLE_YELLOW
from posteryard.render.lines import Label

TODAY = date(2026, 10, 3)


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def text(dates: status.Dates, leaving: str | None = None) -> str | None:
    found = status.label(dates, TODAY, leaving)
    return found.text if found else None


def test_leaving_wins_in_red() -> None:
    found = status.label(status.Dates(added=ago(1)), TODAY, "LEAVING IN 3 DAYS")
    assert found == Label("LEAVING IN 3 DAYS", APPLE_RED)


def test_a_new_title_is_just_added_even_with_new_episodes() -> None:
    dates = status.Dates(added=ago(2), newest_season=ago(2), newest_episode=ago(2))
    assert status.label(dates, TODAY) == Label("JUST ADDED", APPLE_GREEN)
    assert text(status.Dates(added=ago(13))) == "JUST ADDED"
    assert text(status.Dates(added=ago(14))) is None


def test_an_episode_added_later_is_new() -> None:
    dates = status.Dates(added=ago(300), newest_season=ago(200), newest_episode=ago(3))
    assert status.label(dates, TODAY) == Label("NEW EPISODE", APPLE_BLUE)
    assert text(status.Dates(added=ago(300), newest_episode=ago(7))) is None


def test_a_season_added_later_is_a_new_season() -> None:
    assert text(status.Dates(added=ago(300), newest_season=ago(1), newest_episode=ago(1))) == "NEW SEASON"


def test_an_upcoming_season_shows_its_date() -> None:
    found = status.label(status.Dates(added=ago(300), next_season=date(2026, 10, 21)), TODAY)
    assert found == Label("NEW SEASON OCT 21", APPLE_YELLOW)
    assert text(status.Dates(next_season=TODAY)) == "NEW SEASON TODAY"
    assert text(status.Dates(next_season=TODAY + timedelta(days=31))) is None
    assert text(status.Dates(next_season=ago(1))) is None


def test_next_season_needs_a_first_episode() -> None:
    assert status.next_season({"next_episode_to_air": {"episode_number": 1, "air_date": "2026-10-21"}}) == date(
        2026, 10, 21
    )
    assert status.next_season({"next_episode_to_air": {"episode_number": 4, "air_date": "2026-10-21"}}) is None
    assert status.next_season({"next_episode_to_air": {"episode_number": 1, "air_date": None}}) is None
    assert status.next_season({}) is None


def test_timestamps() -> None:
    assert status.from_timestamp(0) is None
    assert status.from_timestamp("x") is None
    assert status.from_timestamp(1789413501) is not None
