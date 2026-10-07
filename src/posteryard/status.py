from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from posteryard.render.layers import APPLE_BLUE, APPLE_GREEN, APPLE_RED, APPLE_YELLOW
from posteryard.render.lines import Label
from posteryard.tmdb import Details

NEW_DAYS = 7
ADDED_DAYS = 14
COMING_DAYS = 30
SAME_ADD = timedelta(days=1)


@dataclass(frozen=True)
class Dates:
    added: date | None = None
    newest_season: date | None = None
    newest_episode: date | None = None
    next_season: date | None = None


def label(dates: Dates, today: date, leaving: str | None = None) -> Label | None:
    if leaving:
        return Label(leaving, APPLE_RED)
    added = dates.added
    if _within(dates.newest_episode, today, NEW_DAYS) and _after(dates.newest_episode, added):
        if _within(dates.newest_season, today, NEW_DAYS) and _after(dates.newest_season, added):
            return Label("NEW SEASON", APPLE_BLUE)
        return Label("NEW EPISODE", APPLE_BLUE)
    if _within(added, today, ADDED_DAYS):
        return Label("JUST ADDED", APPLE_GREEN)
    if dates.next_season and 0 <= (dates.next_season - today).days <= COMING_DAYS:
        when = "TODAY" if dates.next_season == today else f"{dates.next_season:%b} {dates.next_season.day}".upper()
        return Label(f"NEW SEASON {when}", APPLE_YELLOW)
    return None


def _within(day: date | None, today: date, days: int) -> bool:
    return day is not None and 0 <= (today - day).days < days


def _after(day: date | None, added: date | None) -> bool:
    return day is not None and (added is None or day - added > SAME_ADD)


def from_timestamp(value: Any) -> date | None:
    try:
        return date.fromtimestamp(int(value)) if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def next_season(details: Details) -> date | None:
    upcoming = details.get("next_episode_to_air") or {}
    if not isinstance(upcoming, Mapping) or upcoming.get("episode_number") != 1:
        return None
    try:
        return date.fromisoformat(str(upcoming.get("air_date")))
    except ValueError:
        return None
