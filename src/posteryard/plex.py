"""The Plex Media Server calls Posteryard needs."""

import urllib.parse
from collections.abc import Mapping
from typing import Any

from posteryard import http

Item = Mapping[str, Any]


class Plex:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token

    def _url(self, path: str, **params: Any) -> str:
        return f"{self.url}{path}?{urllib.parse.urlencode({**params, 'X-Plex-Token': self.token})}"

    def _get(self, path: str, **params: Any) -> Mapping[str, Any]:
        container: Mapping[str, Any] = http.get_json(self._url(path, **params))["MediaContainer"]
        return container

    def item(self, rating_key: str) -> Item | None:
        try:
            meta = self._get(f"/library/metadata/{_key(rating_key)}", includeGuids=1).get("Metadata") or []
        except http.HttpError as exc:
            if exc.status == 404:
                return None
            raise
        return meta[0] if meta else None

    def children(self, rating_key: str) -> list[Item]:
        items: list[Item] = self._get(f"/library/metadata/{_key(rating_key)}/children", includeGuids=1).get(
            "Metadata", []
        )
        return items


def _key(rating_key: str) -> str:
    if not rating_key.isdigit():
        raise ValueError(f"not a Plex rating key: {rating_key!r}")
    return rating_key


def tmdb_id(item: Item) -> int | None:
    for guid in item.get("Guid") or []:
        value = str(guid.get("id", ""))
        if value.startswith("tmdb://"):
            return int(value.removeprefix("tmdb://"))
    return None
