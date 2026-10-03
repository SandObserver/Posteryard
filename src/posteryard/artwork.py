"""Choose source art from TMDB for each design.

Choices are cached by the list of candidates they were made from, so OCR runs again only when TMDB's list changes.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from posteryard import composition, ocr
from posteryard.render.layers import trim
from posteryard.tmdb import ImageRef, Images

MAX_CANDIDATES = 6
POOL_SIZE = 12
MIN_BACKDROP_WIDTH = 1920
# Logos at least this wide for their height are wordmarks; narrower ones are emblems or stacked badges.
WORDMARK_ASPECT = 1.8
# Logos darker than this vanish on the black fade under them. Saturated reds and yellows stay above it.
VISIBLE_LUMINANCE = 0.12
# Score added per place a candidate sits below TMDB's best voted one, so votes break near ties.
RANK_PENALTY = 0.03

Fetch = Callable[[str], Image.Image]
Read = Callable[[Image.Image], list[ocr.TextLine]]


class ChoiceCache(Protocol):
    def get_choice(self, key: str) -> Mapping[str, Any] | None: ...
    def put_choice(self, key: str, value: Mapping[str, Any]) -> None: ...


class MemoryChoices:
    def __init__(self) -> None:
        self._data: dict[str, Mapping[str, Any]] = {}

    def get_choice(self, key: str) -> Mapping[str, Any] | None:
        return self._data.get(key)

    def put_choice(self, key: str, value: Mapping[str, Any]) -> None:
        self._data[key] = value


@dataclass(frozen=True)
class Picked:
    path: str
    lines: list[ocr.TextLine]


@dataclass(frozen=True)
class Picker:
    cache: ChoiceCache
    fetch: Fetch
    read: Read

    def _cached(
        self, key: str, refs: Sequence[ImageRef], accept: Callable[[list[ocr.TextLine]], bool]
    ) -> Picked | None:
        candidates = [r.path for r in refs[:MAX_CANDIDATES]]
        hit = self.cache.get_choice(key)
        if hit is not None and hit.get("candidates") == candidates:
            path = hit.get("path")
            return Picked(str(path), [ocr.TextLine(*line) for line in hit.get("lines", [])]) if path else None
        picked = None
        for path in candidates:
            lines = self.read(self.fetch(path))
            if accept(lines):
                picked = Picked(path, lines)
                break
        self.cache.put_choice(
            key,
            {
                "candidates": candidates,
                "path": picked.path if picked else None,
                "lines": [[ln.text, ln.score, ln.height, ln.width] for ln in picked.lines] if picked else [],
            },
        )
        return picked

    def textless(self, key: str, refs: Sequence[ImageRef], titles: Sequence[str]) -> Picked | None:
        """Reject any art that OCR finds a title or display text on, whatever its language tag says."""
        return self._cached(f"textless:{key}", refs, lambda lines: _textless(lines, titles))

    def textless_all(
        self, key: str, refs: Sequence[ImageRef], titles: Sequence[str], limit: int = POOL_SIZE
    ) -> list[str]:
        """Every acceptable textless image among the first `limit` candidates, best first."""
        candidates = [r.path for r in refs[:limit]]
        hit = self.cache.get_choice(f"textless-all:{key}")
        if hit is not None and hit.get("candidates") == candidates:
            return [str(p) for p in hit.get("paths", [])]
        paths = [path for path in candidates if _textless(self.read(self.fetch(path)), titles)]
        self.cache.put_choice(f"textless-all:{key}", {"candidates": candidates, "paths": paths})
        return paths

    def textless_art(self, key: str, images: Images, titles: Sequence[str], logo: str | None = None) -> Picked | None:
        """Without a logo, TMDB's best voted textless art. With one, the art the logo reads best on."""
        if logo is None:
            return self.textless(f"{key}:posters", images.textless_posters(), titles) or self.textless(
                f"{key}:backdrops", images.textless_backdrops(), titles
            )
        for kind, refs in (("posters", images.textless_posters()), ("backdrops", images.textless_backdrops())):
            paths = self.textless_all(f"{key}:{kind}", refs, titles, limit=MAX_CANDIDATES)
            if paths:
                return Picked(self._calmest(paths, logo), [])
        return None

    def _calmest(self, paths: Sequence[str], logo: str) -> str:
        def total(rank: int, path: str) -> float:
            cache_key = f"composition:{path}:{logo}"
            hit = self.cache.get_choice(cache_key)
            if hit is None:
                hit = {"score": composition.score(self.fetch(path), trim(self.fetch(logo)))}
                self.cache.put_choice(cache_key, hit)
            return float(hit["score"]) + RANK_PENALTY * rank

        return min(enumerate(paths), key=lambda pair: total(*pair))[1]

    def background(self, key: str, images: Images, titles: Sequence[str]) -> Picked | None:
        refs = sorted(images.textless_backdrops(), key=lambda r: r.width < MIN_BACKDROP_WIDTH)
        return self.textless(f"{key}:background", refs, titles)

    def logo(
        self, key: str, images: Images, languages: Sequence[str] = ("en",), prefer_wordmark: bool = True
    ) -> str | None:
        refs = images.logos_in(languages)[:MAX_CANDIDATES]
        candidates = [r.path for r in refs]
        cache_key = f"logo2:{key}:{','.join(languages)}:{'wordmark' if prefer_wordmark else 'any'}"
        hit = self.cache.get_choice(cache_key)
        if hit is not None and hit.get("candidates") == candidates:
            return str(hit["path"]) if hit.get("path") else None
        visible = [r for r in refs if composition.logo_luminance(trim(self.fetch(r.path))) >= VISIBLE_LUMINANCE]
        wide = [r for r in visible if r.height and r.width / r.height >= WORDMARK_ASPECT] if prefer_wordmark else []
        pool = wide or visible or refs
        path = pool[0].path if pool else None
        self.cache.put_choice(cache_key, {"candidates": candidates, "path": path})
        return path


def _textless(lines: list[ocr.TextLine], titles: Sequence[str]) -> bool:
    return not ocr.shows_title(lines, titles) and not ocr.has_display_text(lines)
