"""Jellyfin, answering with the Plex-shaped items the rest of Posteryard reads. See `server.py`."""

import base64
import json
import re
import urllib.parse
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from posteryard import http
from posteryard.server import Item, Target

TYPES = {"Movie": "movie", "Series": "show", "Season": "season", "Episode": "episode", "BoxSet": "collection"}
INCLUDE = {kind: name for name, kind in TYPES.items()}
LIBRARY_TYPES = {"movies": "movie", "tvshows": "show"}
IMAGE_TYPES: dict[Target, str] = {"poster": "Primary", "thumb": "Primary", "art": "Backdrop"}
FIELDS = "ProviderIds,DateCreated,Tags,Path,MediaStreams,Width,Height"
PAGE = 200
ID = re.compile(r"^[0-9a-f]{32}$")


def _id(item_id: str) -> str:
    if not ID.match(item_id):
        raise ValueError(f"not a Jellyfin item id: {item_id!r}")
    return item_id


def _timestamp(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()) if value else None
    except ValueError:
        return None


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(max(timestamp, 0), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolution(width: int, height: int) -> str:
    if width >= 3200 or height >= 1700:
        return "4k"
    if width >= 1800 or height >= 1000:
        return "1080"
    if width >= 1200 or height >= 700:
        return "720"
    return ""


def _streams(raw: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Jellyfin streams as Plex streams, with the fields `quality` reads."""
    out: list[dict[str, Any]] = []
    for s in raw:
        titles = {"title": s.get("Title"), "displayTitle": s.get("DisplayTitle")}
        if s.get("Type") == "Video":
            kind = str(s.get("VideoRangeType") or "")
            out.append(
                {
                    "streamType": 1,
                    **titles,
                    "DOVIPresent": kind.startswith("DOVI"),
                    "extendedDisplayTitle": "HDR10+" if "HDR10Plus" in kind else "",
                    "colorTrc": "smpte2084" if "HDR10" in kind else "",
                }
            )
        elif s.get("Type") == "Audio":
            out.append({"streamType": 2, **titles, "channels": s.get("Channels"), "profile": s.get("Profile")})
        elif s.get("Type") == "Subtitle":
            out.append(
                {
                    "streamType": 3,
                    **titles,
                    "codec": s.get("Codec"),
                    "hearingImpaired": bool(s.get("IsHearingImpaired")),
                }
            )
    return out


class Jellyfin:
    name = "Jellyfin"
    collections_per_library = False

    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key
        self._user: str | None = None
        self._libraries: list[Item] | None = None

    def _url(self, path: str, **params: Any) -> str:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return f"{self.url}{path}{'?' + query if query else ''}"

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f'MediaBrowser Token="{self.api_key}"', "Accept": "application/json"}

    def _get(self, path: str, **params: Any) -> Any:
        return json.loads(http.request("GET", self._url(path, **params), headers=self._auth) or b"null")

    def _send(self, method: str, path: str, *, body: bytes | None = None, content: str = "", **params: Any) -> bytes:
        headers = {**self._auth, **({"Content-Type": content} if content else {})}
        return http.request(method, self._url(path, **params), headers=headers, data=body, timeout=120)

    def user(self) -> str:
        """Item details need a user. The first administrator sees every library."""
        if self._user is None:
            users = self._get("/Users")
            admin = next(
                (u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), users[0] if users else None
            )
            if admin is None:
                raise http.RequestError("Jellyfin has no user", self.url)
            self._user = str(admin["Id"])
        return self._user

    def sections(self) -> list[Item]:
        if self._libraries is None:
            self._libraries = [
                {
                    "key": str(folder["ItemId"]),
                    "title": str(folder["Name"]),
                    "type": LIBRARY_TYPES[str(folder.get("CollectionType"))],
                    "paths": [str(p).rstrip("/") + "/" for p in folder.get("Locations") or []],
                }
                for folder in self._get("/Library/VirtualFolders")
                if folder.get("CollectionType") in LIBRARY_TYPES
            ]
        return self._libraries

    def _shape(self, raw: Mapping[str, Any]) -> Item:
        kind = TYPES.get(str(raw.get("Type")), str(raw.get("Type", "")).lower())
        item: dict[str, Any] = {
            "ratingKey": str(raw["Id"]),
            "type": kind,
            "title": str(raw.get("Name", "")),
            "addedAt": _timestamp(raw.get("DateCreated")),
            "Label": [{"tag": tag} for tag in raw.get("Tags") or []],
            "Guid": [
                {"id": f"{source}://{value}"}
                for source, name in (("tmdb", "Tmdb"), ("imdb", "Imdb"), ("tvdb", "Tvdb"))
                if (value := (raw.get("ProviderIds") or {}).get(name))
            ],
        }
        for field, source in (("index", "IndexNumber"), ("parentIndex", "ParentIndexNumber")):
            if raw.get(source) is not None:
                item[field] = int(raw[source])
        if kind == "season":
            item["parentRatingKey"] = raw.get("SeriesId")
        elif kind == "episode":
            item["parentRatingKey"] = raw.get("SeasonId")
            item["grandparentRatingKey"] = raw.get("SeriesId")
        path = str(raw.get("Path") or "")
        library = next((s for s in self.sections() if any(path.startswith(p) for p in s["paths"])), None)
        if library is not None:
            item["librarySectionID"] = library["key"]
            item["librarySectionTitle"] = library["title"]
        streams = raw.get("MediaStreams")
        if streams:
            video: Mapping[str, Any] = next((s for s in streams if s.get("Type") == "Video"), {})
            resolution = _resolution(int(video.get("Width") or 0), int(video.get("Height") or 0))
            item["Media"] = [{"videoResolution": resolution, "Part": [{"Stream": _streams(streams)}]}]
        return item

    def item(self, rating_key: str) -> Item | None:
        if not ID.match(rating_key):
            return None
        try:
            raw = self._get(f"/Items/{_id(rating_key)}", userId=self.user())
        except http.HttpError as exc:
            if exc.status in (400, 404):
                return None
            raise
        return self._shape(raw) if raw else None

    def _items(self, **params: Any) -> Iterator[Item]:
        start = 0
        while True:
            page = self._get("/Items", userId=self.user(), Fields=FIELDS, StartIndex=start, Limit=PAGE, **params)
            items = page.get("Items") or []
            yield from (self._shape(raw) for raw in items)
            start += len(items)
            if not items or start >= int(page.get("TotalRecordCount", start)):
                return

    def children(self, rating_key: str) -> list[Item]:
        return list(self._items(ParentId=_id(rating_key), SortBy="IndexNumber"))

    def collection_children(self, rating_key: str) -> list[Item]:
        return list(self._items(ParentId=_id(rating_key)))

    def section_items(self, section_key: str, kind: str, **filters: Any) -> Iterator[Item]:
        """Collections are not inside a library in Jellyfin; every library lists all of them."""
        params: dict[str, Any] = {"IncludeItemTypes": INCLUDE[kind], "Recursive": "true"}
        if kind != "collection":
            params["ParentId"] = _id(section_key)
        if "label" in filters:
            params["Tags"] = filters["label"]
        # Jellyfin has no created-date filter and ignores MinDateCreated. A new item's save date is its added date.
        since = filters.get("updatedAt>>", filters.get("addedAt>>"))
        if since is not None:
            params["MinDateLastSaved"] = _iso(int(since))
        return self._items(**params)

    def changed_since(self, section_key: str, kind: str, since: int) -> list[Item]:
        return list(self.section_items(section_key, kind, **{"updatedAt>>": since}))

    def newest_added(self, section_key: str, kind: str, **filters: Any) -> int | None:
        parent = filters.get("show.id") or filters.get("season.id") or section_key
        page = self._get(
            "/Items",
            userId=self.user(),
            ParentId=_id(str(parent)),
            IncludeItemTypes=INCLUDE[kind],
            Recursive="true",
            SortBy="DateCreated",
            SortOrder="Descending",
            Limit=1,
            Fields="DateCreated",
        )
        items = page.get("Items") or []
        return _timestamp(items[0].get("DateCreated")) if items else None

    def _raw(self, rating_key: str) -> dict[str, Any]:
        raw: dict[str, Any] = self._get(f"/Items/{_id(rating_key)}", userId=self.user())
        return raw

    def selected(self, rating_key: str, target: Target) -> str | None:
        raw = self._raw(rating_key)
        if target == "art":
            tags = raw.get("BackdropImageTags") or []
            return str(tags[0]) if tags else None
        tag = (raw.get("ImageTags") or {}).get("Primary")
        return str(tag) if tag else None

    def upload(self, rating_key: str, target: Target, jpeg: bytes) -> str:
        """Jellyfin takes the image as base64 text. Raw bytes answer HTTP 500."""
        image_type = IMAGE_TYPES[target]
        if target == "art":
            for _ in self._raw(rating_key).get("BackdropImageTags") or []:
                self._send("DELETE", f"/Items/{_id(rating_key)}/Images/Backdrop", imageIndex=0)
        self._send(
            "POST", f"/Items/{_id(rating_key)}/Images/{image_type}", body=base64.b64encode(jpeg), content="image/jpeg"
        )
        tag = self.selected(rating_key, target)
        if not tag:
            raise http.RequestError(f"upload left no {target} to select", self._url(f"/Items/{rating_key}"))
        return tag

    def poster_bytes(self, item: Item) -> bytes:
        return http.request(
            "GET", self._url(f"/Items/{_id(str(item['ratingKey']))}/Images/Primary"), headers=self._auth, timeout=60
        )

    def _update_tags(self, rating_key: str, change: Any) -> None:
        raw = self._raw(rating_key)
        raw["Tags"] = change(list(raw.get("Tags") or []))
        self._send("POST", f"/Items/{_id(rating_key)}", body=json.dumps(raw).encode(), content="application/json")

    def remove_label(self, item: Item, label: str) -> None:
        self._update_tags(str(item["ratingKey"]), lambda tags: [t for t in tags if t.lower() != label.lower()])

    def set_label(self, section_key: str, kind: str, rating_key: str, label: str) -> None:
        self._update_tags(rating_key, lambda _tags: [label])

    def lock(self, item: Item, target: Target) -> None:
        """Nothing to lock: Jellyfin keeps an uploaded image unless a refresh is told to replace images."""

    def restore(self, item: Item, target: Target) -> None:
        key = _id(str(item["ratingKey"]))
        self._send("DELETE", f"/Items/{key}/Images/{IMAGE_TYPES[target]}")
        self._send("POST", f"/Items/{key}/Refresh", imageRefreshMode="FullRefresh", replaceAllImages="false")

    def create_collection(self, section_key: str, kind: str, title: str, rating_keys: list[str]) -> str:
        answer = self._send("POST", "/Collections", name=title, ids=",".join(_id(k) for k in rating_keys))
        return str(json.loads(answer)["Id"])

    def add_to_collection(self, rating_key: str, members: list[str]) -> None:
        self._send("POST", f"/Collections/{_id(rating_key)}/Items", ids=",".join(_id(k) for k in members))

    def remove_from_collection(self, rating_key: str, member: str) -> None:
        self._send("DELETE", f"/Collections/{_id(rating_key)}/Items", ids=_id(member))

    def delete_collection(self, rating_key: str) -> None:
        self._send("DELETE", f"/Items/{_id(rating_key)}")
