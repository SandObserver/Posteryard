import json
import urllib.parse
from collections.abc import Iterator, Mapping
from typing import Any

from posteryard import http
from posteryard.server import Item, Target

# Posters and episode thumbnails share Plex's poster endpoints and its `thumb` field.
ENDPOINTS: dict[Target, tuple[str, str, str]] = {
    "poster": ("posters", "poster", "thumb"),
    "thumb": ("posters", "poster", "thumb"),
    "art": ("arts", "art", "art"),
}
TYPE_IDS = {"movie": 1, "show": 2, "season": 3, "episode": 4, "collection": 18}
PAGE = 200


class Plex:
    name = "Plex"

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self._machine_id: str | None = None

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

    def newest_added(self, section_key: str, kind: str, **filters: Any) -> int | None:
        """The latest `addedAt` among matching items, such as the episodes of one show with `show.id`."""
        page = self._get(
            f"/library/sections/{_key(section_key)}/all",
            type=TYPE_IDS[kind],
            sort="addedAt:desc",
            **filters,
            **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 1},
        )
        items = page.get("Metadata") or []
        return int(items[0]["addedAt"]) if items and items[0].get("addedAt") else None

    def machine_id(self) -> str:
        if self._machine_id is None:
            self._machine_id = str(http.get_json(self._url("/identity"))["MediaContainer"]["machineIdentifier"])
        return self._machine_id

    def _uri(self, rating_keys: list[str]) -> str:
        keys = ",".join(_key(k) for k in rating_keys)
        return f"server://{self.machine_id()}/com.plexapp.plugins.library/library/metadata/{keys}"

    def collection_children(self, rating_key: str) -> list[Item]:
        items: list[Item] = self._get(f"/library/collections/{_key(rating_key)}/children", includeGuids=1).get(
            "Metadata", []
        )
        return items

    def create_collection(self, section_key: str, kind: str, title: str, rating_keys: list[str]) -> str:
        url = self._url(
            "/library/collections",
            type=TYPE_IDS[kind],
            title=title,
            smart=0,
            sectionId=_key(section_key),
            uri=self._uri(rating_keys),
        )
        answer = http.request("POST", url, headers={"Accept": "application/json"})
        created = json.loads(answer)["MediaContainer"]["Metadata"][0]
        return str(created["ratingKey"])

    def add_to_collection(self, rating_key: str, members: list[str]) -> None:
        http.request("PUT", self._url(f"/library/collections/{_key(rating_key)}/items", uri=self._uri(members)))

    def remove_from_collection(self, rating_key: str, member: str) -> None:
        http.request("DELETE", self._url(f"/library/collections/{_key(rating_key)}/items/{_key(member)}"))

    def delete_collection(self, rating_key: str) -> None:
        http.request("DELETE", self._url(f"/library/collections/{_key(rating_key)}"))

    def set_label(self, section_key: str, kind: str, rating_key: str, label: str) -> None:
        """Replace the item's labels with one label."""
        http.request(
            "PUT",
            self._url(
                f"/library/sections/{_key(section_key)}/all",
                type=TYPE_IDS[kind],
                id=_key(rating_key),
                **{"label[0].tag.tag": label, "label.locked": 1},
            ),
        )

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

    def restore(self, item: Item, target: Target) -> None:
        """Select the image Plex's agent chose before any upload, and unlock the field again."""
        rating_key = str(item["ratingKey"])
        _, select, field = ENDPOINTS[target]
        original = next(
            (
                str(i["ratingKey"])
                for i in self.images(rating_key, target)
                if not str(i.get("ratingKey", "")).startswith("upload://")
            ),
            None,
        )
        if original:
            http.request("PUT", self._url(f"/library/metadata/{_key(rating_key)}/{select}", url=original))
        http.request(
            "PUT",
            self._url(
                f"/library/sections/{_key(str(item['librarySectionID']))}/all",
                type=TYPE_IDS[str(item["type"])],
                id=rating_key,
                **{f"{field}.locked": 0},
            ),
        )

    def poster_bytes(self, item: Item) -> bytes:
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


def is_rating_key(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _key(rating_key: str) -> str:
    if not is_rating_key(rating_key):
        raise ValueError(f"not a Plex rating key: {rating_key!r}")
    return rating_key
