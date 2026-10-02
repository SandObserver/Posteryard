"""TMDB lookups and image downloads."""

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
ENGLISH_REGIONS = frozenset({"US", "CA", "GB", "AU", "IE", "NZ"})


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

    def english_posters(self) -> list[ImageRef]:
        return [r for r in self.posters if r.language == "en"]

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

    def english_titles(self, kind: Kind, tmdb_id: int) -> list[str]:
        """The title plus alternative titles used in English-speaking regions."""
        details = self.details(kind, tmdb_id)
        titles = [str(details.get("title") or details.get("name") or "")]
        raw = self._get(f"/{kind}/{tmdb_id}/alternative_titles")
        for alt in raw.get("titles") or raw.get("results") or []:
            if alt.get("iso_3166_1") in ENGLISH_REGIONS:
                titles.append(str(alt.get("title", "")))
        return [t for t in dict.fromkeys(titles) if t]

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
        body = http.request("GET", f"{IMAGES}/{size}{path}", timeout=60)
        return Image.open(io.BytesIO(body))
