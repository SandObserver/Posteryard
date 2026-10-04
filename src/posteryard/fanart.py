"""fanart.tv, the second art source. Tried only when TMDB has no usable art, logo or backdrop."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from posteryard import http
from posteryard.tmdb import ImageRef, Images, Kind, open_image

API = "https://webservice.fanart.tv/v3"
ASSETS = ("https://assets.fanart.tv/", "http://assets.fanart.tv/")
# fanart.tv marks images without text with the language "00".
TEXTLESS = frozenset({"00", ""})
# fanart.tv publishes every image of a type at one size.
POSTER = (1000, 1426)
BACKGROUND = (1920, 1080)
HD_LOGO = (800, 310)
LOGO = (400, 155)
FIELDS: dict[Kind, dict[str, tuple[str, ...]]] = {
    "movie": {"posters": ("movieposter",), "backdrops": ("moviebackground",), "logos": ("hdmovielogo", "movielogo")},
    "tv": {"posters": ("tvposter",), "backdrops": ("showbackground",), "logos": ("hdtvlogo", "clearlogo")},
}
SIZES = {
    "movieposter": POSTER, "tvposter": POSTER, "seasonposter": POSTER,
    "moviebackground": BACKGROUND, "showbackground": BACKGROUND,
    "hdmovielogo": HD_LOGO, "hdtvlogo": HD_LOGO, "movielogo": LOGO, "clearlogo": LOGO,
}  # fmt: skip


@dataclass(frozen=True)
class FanartImages:
    images: Images = field(default_factory=lambda: Images([], [], []))
    seasons: Mapping[int, list[ImageRef]] = field(default_factory=dict)


def is_fanart(path: str) -> bool:
    return path.startswith(ASSETS)


def _refs(raw: Mapping[str, Any], names: Sequence[str]) -> list[ImageRef]:
    refs = []
    for name in names:
        width, height = SIZES[name]
        for entry in raw.get(name) or []:
            url = str(entry.get("url") or "")
            if not is_fanart(url):
                continue
            lang = str(entry.get("lang") or "")
            likes = int(entry.get("likes") or 0) if str(entry.get("likes") or "0").isdigit() else 0
            path = "https://" + url.split("://", 1)[1]
            refs.append(ImageRef(path, None if lang in TEXTLESS else lang, width, height, likes, likes))
    return sorted(refs, key=lambda r: (-r.votes, -r.width))


class Fanart:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def images(self, kind: Kind, tmdb_id: int, tvdb_id: int | None) -> FanartImages:
        """Movies are looked up by TMDB id, shows by TVDB id. Empty when fanart.tv has nothing."""
        if kind == "tv" and not tvdb_id:
            return FanartImages()
        path = f"/movies/{tmdb_id}" if kind == "movie" else f"/tv/{tvdb_id}"
        try:
            raw = http.get_json(f"{API}{path}", {"api-key": self.api_key})
        except http.HttpError as exc:
            if exc.status == 404:
                return FanartImages()
            raise
        if not isinstance(raw, Mapping):
            return FanartImages()
        fields = FIELDS[kind]
        images = Images(_refs(raw, fields["posters"]), _refs(raw, fields["backdrops"]), _refs(raw, fields["logos"]))
        seasons: dict[int, list[ImageRef]] = {}
        for entry in raw.get("seasonposter") or []:
            number = str(entry.get("season") or "")
            if number.isdigit():
                seasons.setdefault(int(number), []).extend(_refs({"seasonposter": [entry]}, ["seasonposter"]))
        for refs in seasons.values():
            refs.sort(key=lambda r: -r.votes)
        return FanartImages(images, seasons)

    @staticmethod
    def image(url: str) -> Image.Image:
        if not url.startswith(ASSETS[0]):
            raise ValueError(f"not a fanart.tv image: {url}")
        return open_image(http.request("GET", url, timeout=60))
