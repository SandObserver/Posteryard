"""The day Maintainerr acts on each Plex item."""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from posteryard import http


def action_days(collections: Iterable[Mapping[str, Any]]) -> dict[str, date]:
    out: dict[str, date] = {}
    for collection in collections:
        days = collection.get("deleteAfterDays")
        if not collection.get("isActive") or not days:
            continue
        for media in collection.get("media") or []:
            key = str(media.get("mediaServerId") or media.get("plexId") or "")
            added = media.get("addDate")
            if not key or not added:
                continue
            day = datetime.fromisoformat(str(added).replace("Z", "+00:00")).date() + timedelta(days=int(days))
            out[key] = min(day, out.get(key, day))
    return out


def days_left(action_day: date | None, today: date) -> int | None:
    if action_day is None or action_day < today:
        return None
    return (action_day - today).days


def label(days: int) -> str:
    if days == 0:
        return "LEAVING TODAY"
    if days == 1:
        return "LEAVING TOMORROW"
    return f"LEAVING IN {days} DAYS"


class Maintainerr:
    def __init__(self, url: str) -> None:
        self.url = url

    def action_days(self) -> dict[str, date]:
        if not self.url:
            return {}
        return action_days(http.get_json(f"{self.url}/api/collections"))
