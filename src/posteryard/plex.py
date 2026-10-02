"""The Plex Media Server calls Posteryard needs."""

import urllib.parse
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from posteryard import http

Item = Mapping[str, Any]
Target = Literal["poster", "art", "thumb"]

# Posters and episode thumbnails share Plex's poster endpoints and its `thumb` field.
ENDPOINTS: dict[Target, tuple[str, str, str]] = {
    "poster": ("posters", "poster", "thumb"),
    "thumb": ("posters", "poster", "thumb"),
    "art": ("arts", "art", "art"),
}
TYPE_IDS = {"movie": 1, "show": 2, "season": 3, "episode": 4}
PAGE = 200


class Plex:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token

    def _url(self, path: str, **params: Any) -> str:
        return f"{self.url}{path}?{urllib.parse.urlencode({**params, 'X-Plex-Token': self.token})}"

    def _get(self, path: str, **params: Any) -> Mapping[str, Any]:
        container: Mapping[str, Any] = http.get_json(self._url(path, **params))["MediaContainer"]
        return container

    def sections(self) -> list[Item]:
        sections: list[Item] = self._get("/library/sections").get("Directory", [])
        return sections

    def item(self, rating_key: str) -> Item | None:
        try:
            meta = self._get(f"/library/metadata/{_key(rating_key)}", includeGuids=1).get("Metadata") or []
        except http.HttpError as exc:
            if exc.status == 404:
                return None
            raise
        return meta[0] if meta else None

    def children(self, rating_key: str) -> list[Item]:
        path = f"/library/metadata/{_key(rating_key)}/children"
        items: list[Item] = self._get(path, includeGuids=1).get("Metadata", [])
        return items

    def section_items(self, section_key: str, kind: str, **filters: Any) -> Iterator[Item]:
        start = 0
        while True:
            page = self._get(
                f"/library/sections/{_key(section_key)}/all",
                type=TYPE_IDS[kind],
                includeGuids=1,
                **filters,
                **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE},
            )
            items = page.get("Metadata", [])
            yield from items
            start += len(items)
            if not items or start >= int(page.get("totalSize", start)):
                return

    def changed_since(self, section_key: str, kind: str, since: int) -> list[Item]:
        """Items added or updated after a Unix time. A replaced file only changes `updatedAt`."""
        added = list(self.section_items(section_key, kind, **{"addedAt>>": since}))
        updated = list(self.section_items(section_key, kind, **{"updatedAt>>": since}))
        return list({str(i["ratingKey"]): i for i in added + updated}.values())

    def images(self, rating_key: str, target: Target) -> list[Item]:
        listing, _, _ = ENDPOINTS[target]
        images: list[Item] = self._get(f"/library/metadata/{_key(rating_key)}/{listing}").get("Metadata", [])
        return images

    def selected(self, rating_key: str, target: Target) -> str | None:
        return next((str(i.get("ratingKey")) for i in self.images(rating_key, target) if i.get("selected")), None)

    def upload(self, rating_key: str, target: Target, jpeg: bytes) -> str:
        """Upload, select, and return the key Plex gave the new image."""
        listing, select, _ = ENDPOINTS[target]
        before = {str(i.get("ratingKey")) for i in self.images(rating_key, target)}
        http.request(
            "POST",
            self._url(f"/library/metadata/{_key(rating_key)}/{listing}"),
            data=jpeg,
            headers={"Content-Type": "image/jpeg"},
            timeout=120,
        )
        after = self.images(rating_key, target)
        new = [str(i["ratingKey"]) for i in after if str(i.get("ratingKey")) not in before]
        image_key = new[0] if new else next((str(i["ratingKey"]) for i in after if i.get("selected")), None)
        if not image_key:
            raise http.RequestError(f"upload left no {target} to select", self._url(f"/library/metadata/{rating_key}"))
        http.request("PUT", self._url(f"/library/metadata/{_key(rating_key)}/{select}", url=image_key))
        return image_key

    def poster_bytes(self, item: Item) -> bytes:
        """The poster Plex currently shows for the item."""
        thumb = str(item.get("thumb") or "")
        if not thumb.startswith("/library/"):
            raise http.RequestError("the item has no poster", self._url(f"/library/metadata/{item.get('ratingKey')}"))
        return http.request("GET", self._url(thumb), timeout=60)

    def remove_label(self, item: Item, label: str) -> None:
        http.request(
            "PUT",
            self._url(
                f"/library/sections/{_key(str(item['librarySectionID']))}/all",
                type=TYPE_IDS[str(item["type"])],
                id=item["ratingKey"],
                **{"label[].tag.tag-": label},
            ),
        )

    def lock(self, item: Item, target: Target) -> None:
        """Lock the field so a metadata refresh keeps the uploaded image."""
        _, _, field = ENDPOINTS[target]
        http.request(
            "PUT",
            self._url(
                f"/library/sections/{_key(str(item['librarySectionID']))}/all",
                type=TYPE_IDS[str(item["type"])],
                id=item["ratingKey"],
                **{f"{field}.locked": 1},
            ),
        )


def labels(item: Item) -> set[str]:
    return {str(label.get("tag", "")).lower() for label in item.get("Label") or []}


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
