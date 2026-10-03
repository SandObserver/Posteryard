import io
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image

from posteryard import http

Kind = Literal["movie", "tv"]
API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p"
# The largest side any design draws: a 1500 px poster, a 1920 px background, with margin for crops.
MAX_SIDE = 2160
# Larger images are refused before decoding. Decoding one costs about 3 bytes per pixel.
MAX_PIXELS = 50_000_000
# TMDB serves this size for posters, backdrops and logos. Used when an original is over MAX_PIXELS.
FALLBACK_SIZE = "w1280"


@dataclass(frozen=True)
class ImageRef:
    path: str
    language: str | None
    width: int
    height: int
    votes: float
    vote_count: int


def _refs(raw: Sequence[Mapping[str, Any]]) -> list[ImageRef]:
    refs = [
        ImageRef(
            path=str(r["file_path"]),
            language=r.get("iso_639_1") or None,
            width=int(r.get("width") or 0),
            height=int(r.get("height") or 0),
            votes=float(r.get("vote_average") or 0),
            vote_count=int(r.get("vote_count") or 0),
        )
        for r in raw
        if r.get("file_path")
    ]
    return sorted(refs, key=lambda r: (-r.votes, -r.vote_count, -r.width))


@dataclass(frozen=True)
class Images:
    posters: list[ImageRef]
    backdrops: list[ImageRef]
    logos: list[ImageRef]

    def textless_posters(self) -> list[ImageRef]:
        return [r for r in self.posters if r.language in (None, "xx")]

    def textless_backdrops(self) -> list[ImageRef]:
        return [r for r in self.backdrops if r.language in (None, "xx")]

    def textless_art(self) -> list[ImageRef]:
        return self.textless_posters() + self.textless_backdrops()

    def english_logos(self) -> list[ImageRef]:
        return [r for r in self.logos if r.language == "en" and r.path.endswith(".png")]


class Tmdb:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def _get(self, path: str, **params: Any) -> Any:
        return http.get_json(f"{API}{path}?{urllib.parse.urlencode({**params, 'api_key': self.api_key})}")

    def details(self, kind: Kind, tmdb_id: int) -> Mapping[str, Any]:
        details: Mapping[str, Any] = self._get(f"/{kind}/{tmdb_id}")
        return details

    def images(self, kind: Kind, tmdb_id: int) -> Images:
        raw = self._get(f"/{kind}/{tmdb_id}/images", include_image_language="en,null,xx")
        return Images(_refs(raw.get("posters", [])), _refs(raw.get("backdrops", [])), _refs(raw.get("logos", [])))

    def season_images(self, show_id: int, season: int) -> Images:
        try:
            raw = self._get(f"/tv/{show_id}/season/{season}/images", include_image_language="en,null,xx")
        except http.HttpError as exc:
            if exc.status == 404:
                return Images([], [], [])
            raise
        return Images(_refs(raw.get("posters", [])), [], [])

    def episode(self, show_id: int, season: int, episode: int) -> Mapping[str, Any] | None:
        try:
            data: Mapping[str, Any] = self._get(f"/tv/{show_id}/season/{season}/episode/{episode}")
        except http.HttpError as exc:
            if exc.status == 404:
                return None
            raise
        return data

    def all_titles(self, kind: Kind, tmdb_id: int) -> list[str]:
        details = self.details(kind, tmdb_id)
        titles = [str(details.get(k) or "") for k in ("title", "name", "original_title", "original_name")]
        raw = self._get(f"/{kind}/{tmdb_id}/alternative_titles")
        titles += [str(alt.get("title", "")) for alt in raw.get("titles") or raw.get("results") or []]
        return [t for t in dict.fromkeys(titles) if t]

    def watch_providers(self, kind: Kind, tmdb_id: int) -> Mapping[str, Any]:
        results: Mapping[str, Any] = self._get(f"/{kind}/{tmdb_id}/watch/providers").get("results", {})
        return results

    @staticmethod
    def image(path: str, size: str = "original") -> Image.Image:
        try:
            return open_image(http.request("GET", f"{IMAGES}/{size}{path}", timeout=60))
        except ValueError:
            if size == FALLBACK_SIZE:
                raise
            return open_image(http.request("GET", f"{IMAGES}/{FALLBACK_SIZE}{path}", timeout=60))


def open_image(data: bytes) -> Image.Image:
    """Decoded and shrunk to MAX_SIDE. Full-size TMDB originals reach 70 MB each in memory.

    Raises ValueError for an image over MAX_PIXELS and OSError for data that is not an image.
    """
    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise ValueError("the image has too many pixels") from None
    if image.width * image.height > MAX_PIXELS:
        raise ValueError(f"the image is too large: {image.width} x {image.height} pixels")
    image.draft("RGB", (MAX_SIDE, MAX_SIDE))
    image.load()
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return image
