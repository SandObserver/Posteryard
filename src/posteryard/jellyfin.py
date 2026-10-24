"""Jellyfin and Emby, answering with the Plex-shaped items the rest of Posteryard reads. See `server.py`."""

import base64
import gzip
import io
import json
import logging
import re
import urllib.parse
import zlib
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any
from xml.parsers import expat

from posteryard import http
from posteryard.server import Guide, Item, Target

TYPES = {"Movie": "movie", "Series": "show", "Season": "season", "Episode": "episode", "BoxSet": "collection"}
INCLUDE = {kind: name for name, kind in TYPES.items()}
LIBRARY_TYPES = {"movies": "movie", "tvshows": "show"}
IMAGE_TYPES: dict[Target, str] = {"poster": "Primary", "thumb": "Primary", "art": "Backdrop"}
FIELDS = "ProviderIds,DateCreated,Tags,Path,MediaStreams,Width,Height,ProductionYear"
PAGE = 200
# Jellyfin answers HTTP 414 to a URL over 8 KB. 100 ids keep a collection request near 3.5 KB.
IDS_PER_REQUEST = 100
PROVIDERS = (("tmdb", "tmdb"), ("imdb", "imdb"), ("tvdb", "tvdb"))
GUIDE = "guide"
log = logging.getLogger(__name__)
XMLTV_TIME = "%Y%m%d%H%M%S %z"
XMLTV_MAX_EVENTS = 8_000_000
XMLTV_MAX_DEPTH = 16


def _timestamp(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()) if value else None
    except ValueError:
        return None


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(max(timestamp, 0), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _folder(path: str) -> str:
    """Windows servers report paths with backslashes."""
    return path.replace("\\", "/").rstrip("/") + "/"


def _resolution(width: int, height: int) -> str:
    if width >= 3200 or height >= 1700:
        return "4k"
    if width >= 1800 or height >= 1000:
        return "1080"
    if width >= 1200 or height >= 700:
        return "720"
    return ""


def _streams(raw: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in raw:
        titles = {"title": s.get("Title"), "displayTitle": s.get("DisplayTitle")}
        if s.get("Type") == "Video":
            kind = str(s.get("VideoRangeType") or s.get("ExtendedVideoType") or "").lower()
            pq = "hdr10" in kind or str(s.get("ColorTransfer") or "").lower() == "smpte2084"
            out.append(
                {
                    "streamType": 1,
                    **titles,
                    "DOVIPresent": kind.startswith(("dovi", "dolbyvision")),
                    "extendedDisplayTitle": "HDR10+" if "hdr10plus" in kind else "",
                    "colorTrc": "smpte2084" if pq else "",
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


def _media(streams: list[Mapping[str, Any]]) -> dict[str, Any]:
    video: Mapping[str, Any] = next((s for s in streams if s.get("Type") == "Video"), {})
    resolution = _resolution(int(video.get("Width") or 0), int(video.get("Height") or 0))
    return {"videoResolution": resolution, "Part": [{"Stream": _streams(streams)}]}


class Jellyfin:
    name = "Jellyfin"
    collections_per_library = False
    item_id = re.compile(r"^[0-9a-f]{32}$")

    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key
        self._user: str | None = None
        self._libraries: list[Item] | None = None

    def _id(self, item_id: str) -> str:
        if not self.item_id.match(item_id):
            raise ValueError(f"not a valid {self.name} item id: {item_id!r}")
        return item_id

    def _url(self, path: str, **params: Any) -> str:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return f"{self.url}{path}{'?' + query if query else ''}"

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f'MediaBrowser Token="{self.api_key}"', "Accept": "application/json"}

    def _get(self, path: str, **params: Any) -> Any:
        return json.loads(http.request("GET", self._url(path, **params), headers=self._auth) or b"null")

    def _send(
        self, method: str, path: str, *, body: bytes | None = None, content: str = "", retries: int = 3, **params: Any
    ) -> bytes:
        headers = {**self._auth, **({"Content-Type": content} if content else {})}
        return http.request(method, self._url(path, **params), headers=headers, data=body, timeout=120, retries=retries)

    def user(self) -> str:
        if self._user is None:
            users = self._get("/Users")
            admin = next(
                (u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), users[0] if users else None
            )
            if admin is None:
                raise http.RequestError(f"{self.name} has no user", self.url)
            self._user = str(admin["Id"])
        return self._user

    def server_id(self) -> str:
        server = str((self._get("/System/Info/Public") or {}).get("Id") or "")
        if not server:
            raise http.RequestError(f"{self.name} did not report its server ID", self.url)
        return f"{self.name.lower()}:{server}"

    def sections(self) -> list[Item]:
        if self._libraries is None:
            self._libraries = [
                {
                    "key": str(folder["ItemId"]),
                    "title": str(folder["Name"]),
                    "type": LIBRARY_TYPES[str(folder.get("CollectionType"))],
                    "paths": [_folder(str(p)) for p in folder.get("Locations") or []],
                }
                for folder in self._get("/Library/VirtualFolders")
                if folder.get("CollectionType") in LIBRARY_TYPES
            ]
        return self._libraries

    def _tags(self, raw: Mapping[str, Any]) -> list[str]:
        return [str(tag) for tag in raw.get("Tags") or []]

    def _shape(self, raw: Mapping[str, Any]) -> Item:
        kind = TYPES.get(str(raw.get("Type")), str(raw.get("Type", "")).lower())
        providers = {str(name).lower(): value for name, value in (raw.get("ProviderIds") or {}).items()}
        item: Item = {
            "ratingKey": str(raw["Id"]),
            "type": kind,
            "title": str(raw.get("Name", "")),
            "addedAt": _timestamp(raw.get("DateCreated")),
            "Label": [{"tag": tag} for tag in self._tags(raw)],
            "Guid": [{"id": f"{source}://{value}"} for source, name in PROVIDERS if (value := providers.get(name))],
        }
        if raw.get("ProductionYear"):
            item["year"] = int(raw["ProductionYear"])
        if raw.get("IndexNumber") is not None:
            item["index"] = int(raw["IndexNumber"])
        if raw.get("ParentIndexNumber") is not None:
            item["parentIndex"] = int(raw["ParentIndexNumber"])
        if kind == "season":
            item["parentRatingKey"] = str(raw.get("SeriesId") or "")
        elif kind == "episode":
            item["parentRatingKey"] = str(raw.get("SeasonId") or "")
            item["grandparentRatingKey"] = str(raw.get("SeriesId") or "")
        path = _folder(self._path(raw)).rstrip("/")
        library = next((s for s in self.sections() if any(path.startswith(p) for p in s["paths"])), None)
        if library is not None:
            item["librarySectionID"] = library["key"]
            item["librarySectionTitle"] = library["title"]
        versions = [s.get("MediaStreams") for s in raw.get("MediaSources") or []] or [raw.get("MediaStreams")]
        media = [_media(streams) for streams in versions if streams]
        if media:
            item["Media"] = media
        return item

    def _path(self, raw: Mapping[str, Any]) -> str:
        """A season of a show without season folders has no path. Its library is the show's."""
        path = str(raw.get("Path") or "")
        series = str(raw.get("SeriesId") or "")
        if path or not series or not self.item_id.match(series):
            return path
        try:
            return str(self._raw(series).get("Path") or "")
        except http.HttpError as exc:
            if exc.status in (400, 404):
                return ""
            raise

    def item(self, rating_key: str) -> Item | None:
        if not self.item_id.match(rating_key):
            return None
        try:
            raw = self._raw(rating_key)
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
        return list(self._items(ParentId=self._id(rating_key), SortBy="IndexNumber"))

    def collection_children(self, rating_key: str) -> list[Item]:
        return list(self._items(ParentId=self._id(rating_key)))

    def section_items(self, section_key: str, kind: str, **filters: Any) -> Iterator[Item]:
        """Without collections_per_library, collections are not inside a library; every library lists all of them."""
        params: dict[str, Any] = {"IncludeItemTypes": INCLUDE[kind], "Recursive": "true"}
        if kind != "collection" or self.collections_per_library:
            params["ParentId"] = self._id(section_key)
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
            ParentId=self._id(str(parent)),
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
        raw: dict[str, Any] = self._get(f"/Items/{self._id(rating_key)}", userId=self.user())
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
                self._send("DELETE", f"/Items/{self._id(rating_key)}/Images/Backdrop", imageIndex=0)
        self._send(
            "POST",
            f"/Items/{self._id(rating_key)}/Images/{image_type}",
            body=base64.b64encode(jpeg),
            content="image/jpeg",
        )
        tag = self.selected(rating_key, target)
        if not tag:
            raise http.RequestError(f"upload left no {target} to select", self._url(f"/Items/{rating_key}"))
        return tag

    def poster_bytes(self, item: Item) -> bytes:
        return http.request(
            "GET",
            self._url(f"/Items/{self._id(str(item['ratingKey']))}/Images/Primary"),
            headers=self._auth,
            timeout=60,
        )

    def _save_tags(self, raw: dict[str, Any], tags: list[str]) -> None:
        raw["Tags"] = tags

    def _update_tags(self, rating_key: str, change: Any) -> None:
        raw = self._raw(rating_key)
        self._save_tags(raw, change(self._tags(raw)))
        self._send("POST", f"/Items/{self._id(rating_key)}", body=json.dumps(raw).encode(), content="application/json")

    def remove_label(self, item: Item, label: str) -> None:
        self._update_tags(str(item["ratingKey"]), lambda tags: [t for t in tags if t.lower() != label.lower()])

    def set_label(self, section_key: str, kind: str, rating_key: str, label: str) -> None:
        self._update_tags(rating_key, lambda _tags: [label])

    def lock(self, item: Item, target: Target) -> None:
        """Nothing to lock: Jellyfin keeps an uploaded image unless a refresh is told to replace images."""

    def restore(self, item: Item, target: Target) -> None:
        key = self._id(str(item["ratingKey"]))
        self._send("DELETE", f"/Items/{key}/Images/{IMAGE_TYPES[target]}")
        self._send("POST", f"/Items/{key}/Refresh", imageRefreshMode="FullRefresh", replaceAllImages="false")

    def create_collection(self, section_key: str, kind: str, title: str, rating_keys: list[str]) -> str:
        first, rest = rating_keys[:IDS_PER_REQUEST], rating_keys[IDS_PER_REQUEST:]
        answer = self._send("POST", "/Collections", retries=1, name=title, ids=",".join(self._id(k) for k in first))
        key = str(json.loads(answer)["Id"])
        if rest:
            self.add_to_collection(key, rest)
        return key

    def add_to_collection(self, rating_key: str, members: list[str]) -> None:
        for start in range(0, len(members), IDS_PER_REQUEST):
            ids = ",".join(self._id(k) for k in members[start : start + IDS_PER_REQUEST])
            self._send("POST", f"/Collections/{self._id(rating_key)}/Items", ids=ids)

    def remove_from_collection(self, rating_key: str, member: str) -> None:
        self._send("DELETE", f"/Collections/{self._id(rating_key)}/Items", ids=self._id(member))

    def delete_collection(self, rating_key: str) -> None:
        self._send("DELETE", f"/Items/{self._id(rating_key)}")

    def guides(self) -> list[Guide]:
        task = next((t for t in self._get("/ScheduledTasks") or [] if t.get("Key") == "RefreshGuide"), None)
        finished = _timestamp(((task or {}).get("LastExecutionResult") or {}).get("EndTimeUtc"))
        return [Guide(GUIDE, finished or 0)]

    def guide_airings(self, provider: str, start: int, end: int) -> list[Item]:
        """Every airing is its own item. Airings of one title on one channel share a show key."""
        pictures = self._guide_pictures(start, end)
        channels = self._get("/LiveTv/Channels", userId=self.user(), EnableImages="true").get("Items") or []
        logos = {str(c.get("Id")) for c in channels if (c.get("ImageTags") or {}).get("Primary")}
        airings: list[Item] = []
        while True:
            page = self._get(
                "/LiveTv/Programs", userId=self.user(), MinEndDate=_iso(start), MaxStartDate=_iso(end),
                StartIndex=len(airings), Limit=PAGE, EnableImages="false", Fields="ChannelInfo",
            )  # fmt: skip
            raw = page.get("Items") or []
            airings += [self._airing(p, pictures, logos) for p in raw]
            if not raw or len(airings) >= int(page.get("TotalRecordCount") or len(airings)):
                return airings

    def _airing(self, raw: Mapping[str, Any], pictures: Mapping[tuple[str, int], str], logos: set[str]) -> Item:
        channel, title = str(raw.get("ChannelName") or ""), str(raw.get("Name") or "")
        begins, ends = _timestamp(raw.get("StartDate")) or 0, _timestamp(raw.get("EndDate")) or 0
        show = f"{raw.get('ChannelId')}/{title.casefold()}"
        picture = pictures.get((channel.casefold(), begins), "")
        channel_id = self._id(str(raw.get("ChannelId")))
        logo = self._url(f"/Items/{channel_id}/Images/Primary") if channel_id in logos else ""
        media = {"channelCallSign": channel, "beginsAt": begins, "endsAt": ends, "channelThumb": logo}
        if raw.get("IsMovie"):
            airing = {"type": "movie", "ratingKey": show, "title": title, "thumb": picture, "guid": show}
        else:
            airing = {"type": "episode", "grandparentRatingKey": show, "grandparentTitle": title,
                      "grandparentThumb": picture, "grandparentGuid": show}  # fmt: skip
        return {**airing, "targetKey": self._id(str(raw.get("Id"))), "Media": [media]}  # type: ignore[typeddict-item]

    def _guide_pictures(self, start: int, end: int) -> dict[tuple[str, int], str]:
        """Jellyfin keeps its copy of a guide picture, not the address. The XMLTV guides it reads still have it.

        Guide pictures are optional: a guide Posteryard cannot read gives none, and posters go on without them.
        """
        pictures: dict[tuple[str, int], str] = {}
        for listing in (self._get("/System/Configuration/livetv") or {}).get("ListingProviders") or []:
            path = str(listing.get("Path") or "")
            if listing.get("Type") != "xmltv" or not path.startswith(("http://", "https://")):
                continue
            try:
                data = http.get_checked_redirects(path, timeout=120)
                pictures |= _xmltv_pictures(_unzip(data) if data[:2] == b"\x1f\x8b" else data, start, end)
            except (http.RequestError, expat.ExpatError, OSError, EOFError, ValueError, zlib.error) as exc:
                reason = str(exc) if isinstance(exc, http.RequestError) else type(exc).__name__
                log.warning("guide pictures skipped", extra={"guide": http.redact(path), "reason": reason})
        return pictures

    def guide_target(self, provider: str, key: str, guid: str) -> str | None:
        return key

    def guide_images(self, provider: str, key: str) -> list[Item]:
        tag = self.selected(key, "poster")
        return [{"ratingKey": tag, "selected": True}] if tag else []  # type: ignore[typeddict-unknown-key]

    def guide_upload(self, provider: str, key: str, jpeg: bytes) -> str:
        return self.upload(key, "poster", jpeg)

    def guide_select(self, provider: str, key: str, image_key: str) -> None:
        raise ValueError(f"{self.name} keeps no earlier upload to select")


def _unzip(data: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        out = stream.read(http.MAX_RESPONSE + 1)
    if len(out) > http.MAX_RESPONSE:
        raise ValueError("the XMLTV guide is larger than 64 MB unpacked")
    return out


def _xmltv_pictures(data: bytes, start: int, end: int) -> dict[tuple[str, int], str]:
    reader = _XmltvReader(start, end)
    parser = expat.ParserCreate()
    parser.EntityDeclHandler = reader.entity
    parser.StartElementHandler = reader.open
    parser.EndElementHandler = reader.close
    parser.CharacterDataHandler = reader.text
    parser.Parse(data, True)
    return reader.pictures


class _XmltvReader:
    """Keeps only the channel names and programme pictures. A guide that is kept whole can fill the container memory.

    Refuse every entity declaration. Expat lets entities expand to 100 times the guide.
    """

    def __init__(self, start: int, end: int) -> None:
        self.start, self.end = start, end
        self.names: dict[str, str] = {}
        self.pictures: dict[tuple[str, int], str] = {}
        self.found: dict[str, str] = {}
        self.programme: dict[str, str] = {}
        self.tags: list[str] = []
        self.events = 0
        self.name: list[str] = []

    def entity(self, name: str, *_: object) -> None:
        raise ValueError("the XMLTV guide declares entities")

    def open(self, tag: str, attrs: dict[str, str]) -> None:
        self.events += 1
        self.tags.append(tag)
        if self.events > XMLTV_MAX_EVENTS or len(self.tags) > XMLTV_MAX_DEPTH:
            raise ValueError("the XMLTV guide has too many elements")
        if tag in ("channel", "programme"):
            self.found, self.programme = {}, attrs
        elif tag == "display-name":
            self.name = []
        elif tag == "icon":
            self.found.setdefault("icon", attrs.get("src", ""))

    def text(self, data: str) -> None:
        if self.tags[-1] == "display-name" and "name" not in self.found:
            self.name.append(data)

    def close(self, tag: str) -> None:
        self.tags.pop()
        if tag == "display-name":
            self.found.setdefault("name", "".join(self.name))
        elif tag == "channel":
            self.names[self.programme.get("id", "")] = self.found.get("name", "").casefold()
        elif tag == "programme" and (picture := self._picture()):
            self.pictures[picture[0]] = picture[1]

    def _picture(self) -> tuple[tuple[str, int], str] | None:
        src = self.found.get("icon", "")
        channel = self.names.get(self.programme.get("channel", ""))
        try:
            begins = int(datetime.strptime(self.programme.get("start", ""), XMLTV_TIME).timestamp())
        except ValueError:
            begins = 0
        if src.startswith(("http://", "https://")) and channel is not None and self.start - 86400 < begins < self.end:
            return (channel, begins), src
        return None


class Emby(Jellyfin):
    """Emby answers like Jellyfin except for numeric item ids, the item route, tags and per-library collections."""

    name = "Emby"
    collections_per_library = True
    item_id = re.compile(r"^\d{1,12}$")

    def _raw(self, rating_key: str) -> dict[str, Any]:
        raw: dict[str, Any] = self._get(f"/Users/{self.user()}/Items/{self._id(rating_key)}")
        return raw

    def _tags(self, raw: Mapping[str, Any]) -> list[str]:
        return [str(tag.get("Name")) for tag in raw.get("TagItems") or [] if tag.get("Name")]

    def _save_tags(self, raw: dict[str, Any], tags: list[str]) -> None:
        """Emby ignores `Tags` on save and reads `TagItems` only."""
        raw["TagItems"] = [{"Name": tag} for tag in tags]
