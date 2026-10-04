import io
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
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
# One request returns a title's details, images, alternative titles and providers. Kept for a while, bounded.
CACHE_SECONDS = 600
CACHE_SIZE = 64
TITLE_PARTS = "images,alternative_titles,watch/providers,external_ids"


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

    def logos_in(self, languages: Sequence[str]) -> list[ImageRef]:
        """PNG logos in the first language that has any, best first."""
        for language in languages:
            found = [r for r in self.logos if r.language == language and r.path.endswith(".png")]
            if found:
                return found
        return []


class Tmdb:
    def __init__(self, api_key: str, languages: Sequence[str] = ("en",)) -> None:
        self.api_key = api_key
        self.languages = tuple(languages)
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def _image_languages(self) -> str:
        return ",".join(dict.fromkeys([*self.languages, "en", "null", "xx"]))

    @property
    def _is_token(self) -> bool:
        """TMDB's Read Access Token is a JWT. The short API Key has no dots."""
        return self.api_key.count(".") == 2

    def _get(self, path: str, **params: Any) -> Any:
        if self._is_token:
            url = f"{API}{path}?{urllib.parse.urlencode(params)}"
            return http.get_json(url, {"Authorization": f"Bearer {self.api_key}"})
        return http.get_json(f"{API}{path}?{urllib.parse.urlencode({**params, 'api_key': self.api_key})}")

    def _cached(self, key: str, load: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < CACHE_SECONDS:
                self._cache.move_to_end(key)
                return hit[1]
        value = load()
        with self._lock:
            self._cache[key] = (now, value)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return value

    def details(self, kind: Kind, tmdb_id: int) -> Mapping[str, Any]:
        """Details with `images`, `alternative_titles` and `watch/providers` appended."""
        details: Mapping[str, Any] = self._cached(
            f"{kind}/{tmdb_id}",
            lambda: self._get(
                f"/{kind}/{tmdb_id}", append_to_response=TITLE_PARTS, include_image_language=self._image_languages
            ),
        )
        return details

    def tvdb_id(self, kind: Kind, tmdb_id: int) -> int | None:
        value = (self.details(kind, tmdb_id).get("external_ids") or {}).get("tvdb_id")
        return int(value) if value else None

    def images(self, kind: Kind, tmdb_id: int) -> Images:
        raw = self.details(kind, tmdb_id).get("images") or {}
        return Images(_refs(raw.get("posters", [])), _refs(raw.get("backdrops", [])), _refs(raw.get("logos", [])))

    def _season(self, show_id: int, season: int) -> Mapping[str, Any]:
        """A season with its episodes and `images`. Empty when TMDB has no such season."""

        def load() -> Mapping[str, Any]:
            try:
                data: Mapping[str, Any] = self._get(
                    f"/tv/{show_id}/season/{season}",
                    append_to_response="images",
                    include_image_language=self._image_languages,
                )
            except http.HttpError as exc:
                if exc.status == 404:
                    return {}
                raise
            return data

        season_data: Mapping[str, Any] = self._cached(f"tv/{show_id}/season/{season}", load)
        return season_data

    def season_images(self, show_id: int, season: int) -> Images:
        raw = self._season(show_id, season).get("images") or {}
        return Images(_refs(raw.get("posters", [])), [], [])

    def episode(self, show_id: int, season: int, episode: int) -> Mapping[str, Any] | None:
        episodes = self._season(show_id, season).get("episodes") or []
        return next((e for e in episodes if e.get("episode_number") == episode), None)

    def all_titles(self, kind: Kind, tmdb_id: int) -> list[str]:
        details = self.details(kind, tmdb_id)
        titles = [str(details.get(k) or "") for k in ("title", "name", "original_title", "original_name")]
        raw = details.get("alternative_titles") or {}
        titles += [str(alt.get("title", "")) for alt in raw.get("titles") or raw.get("results") or []]
        return [t for t in dict.fromkeys(titles) if t]

    def find(self, source: str, external_id: str) -> dict[str, int]:
        """TMDB ids by kind for an IMDb or TVDB id. Empty when TMDB does not know it."""
        raw = self._get(f"/find/{urllib.parse.quote(external_id, safe='')}", external_source=f"{source}_id")
        found: dict[str, int] = {}
        for kind, field in (("movie", "movie_results"), ("tv", "tv_results")):
            results = raw.get(field) or []
            if results and results[0].get("id"):
                found[kind] = int(results[0]["id"])
        return found

    def watch_providers(self, kind: Kind, tmdb_id: int) -> Mapping[str, Any]:
        results: Mapping[str, Any] = (self.details(kind, tmdb_id).get("watch/providers") or {}).get("results", {})
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
