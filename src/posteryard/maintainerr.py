from collections.abc import Mapping
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from posteryard import http


def action_days(collections: Any, zone: tzinfo | None = None) -> dict[str, date]:
    if not isinstance(collections, list):
        raise ValueError("Maintainerr did not return a list of collections")
    out: dict[str, date] = {}
    for collection in collections:
        if not isinstance(collection, Mapping):
            continue
        days = collection.get("deleteAfterDays")
        media_list = collection.get("media")
        if not collection.get("isActive") or not days or not isinstance(media_list, list):
            continue
        for media in media_list:
            if not isinstance(media, Mapping):
                continue
            key = str(media.get("mediaServerId") or media.get("plexId") or "")
            added = media.get("addDate")
            if not key or not added:
                continue
            try:
                added_at = datetime.fromisoformat(str(added).replace("Z", "+00:00")).astimezone(zone)
                day = added_at.date() + timedelta(days=int(days))
            except OverflowError:
                continue
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
