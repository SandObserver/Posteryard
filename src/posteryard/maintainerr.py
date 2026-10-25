import logging
import urllib.parse
from collections.abc import Mapping
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from posteryard import http

log = logging.getLogger(__name__)


def action_days(collections: Any, zone: tzinfo | None = None) -> dict[str, date]:
    if not isinstance(collections, list):
        raise ValueError("Maintainerr did not return a list of collections")
    out: dict[str, date] = {}
    for collection in collections:
        if not isinstance(collection, Mapping):
            continue
        for key, day in _collection_days(collection, zone):
            out[key] = min(day, out.get(key, day))
    return out


def _collection_days(collection: Mapping[str, Any], zone: tzinfo | None) -> list[tuple[str, date]]:
    days = collection.get("deleteAfterDays")
    media_list = collection.get("media")
    if not collection.get("isActive") or not days or not isinstance(media_list, list):
        return []
    name = str(collection.get("title", ""))
    try:
        after = timedelta(days=int(days))
    except (TypeError, ValueError, OverflowError):
        log.warning("Maintainerr collection skipped, its delete days are not a number", extra={"collection": name})
        return []
    found: list[tuple[str, date]] = []
    skipped = 0
    for media in media_list:
        if not isinstance(media, Mapping):
            continue
        key = str(media.get("mediaServerId") or media.get("plexId") or "")
        added = media.get("addDate")
        if not key or not added:
            continue
        try:
            found.append(
                (key, datetime.fromisoformat(str(added).replace("Z", "+00:00")).astimezone(zone).date() + after)
            )
        except (ValueError, OverflowError):
            skipped += 1
    if skipped:
        log.warning(
            "Maintainerr titles skipped, their add date is not a date", extra={"collection": name, "count": skipped}
        )
    return found


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
        collections = http.get_json(f"{self.url}/api/collections")
        if isinstance(collections, list):
            collections = [self._with_all_media(c) for c in collections]
        return action_days(collections)

    def _with_all_media(self, collection: Any) -> Any:
        if (
            not isinstance(collection, Mapping)
            or not collection.get("isActive")
            or not collection.get("deleteAfterDays")
        ):
            return collection
        # The collection list holds only a preview of each collection's media.
        query = urllib.parse.urlencode({"collectionId": collection.get("id")})
        return {**collection, "media": http.get_json(f"{self.url}/api/collections/media/?{query}")}
