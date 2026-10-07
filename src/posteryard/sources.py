import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, cast

from PIL import Image

from posteryard import apple, http, ocr, overrides, services
from posteryard.artwork import ChoiceCache, MemoryChoices, Picked, Picker
from posteryard.automarks import AutoMarks
from posteryard.fanart import Fanart, FanartImages, is_fanart
from posteryard.overrides import Override
from posteryard.render.layers import preferred_family
from posteryard.settings import Settings
from posteryard.tmdb import Details, ImageRef, Images, Kind, RegionOffers, Tmdb, open_image

log = logging.getLogger(__name__)
TITLE_CACHE_SECONDS = 600
APPLE_ART_DAYS = 30
APPLE_RETRY_SECONDS = 3600
LOOKUP_SIZE = 4096
DETAIL_FIELDS = ("title", "name", "seasons", "next_episode_to_air")
# Downloaded images held in memory. Unbounded, a long-running service runs out of memory.
FETCH_CACHE = 8


def fetch_art(path: str) -> Image.Image:
    if apple.is_apple(path):
        return open_image(http.request("GET", path, timeout=60, redirects=False))
    return Fanart.image(path) if is_fanart(path) else Tmdb.image(path)


@dataclass(frozen=True)
class Title:
    kind: Kind
    tmdb_id: int
    name: str
    all_titles: list[str]
    service: str | None
    font: str = ""


@dataclass
class Sources:
    tmdb: Tmdb
    settings: Settings
    choices: ChoiceCache = field(default_factory=MemoryChoices)
    read: Callable[[Image.Image], list[ocr.TextLine]] = ocr.read
    overrides: Callable[[str], Override | None] = field(default=lambda _key: None)
    fetch: Callable[[str], Image.Image] = field(default_factory=lambda: lru_cache(maxsize=FETCH_CACHE)(fetch_art))
    fanart: Fanart | None = None
    marks: AutoMarks | None = None
    titles: dict[tuple[Kind, int], tuple[float, Title]] = field(default_factory=dict)
    apple_down_until: float = 0.0
    apple_dropped: bool = False
    lookups: OrderedDict[tuple[str, ...], tuple[float, Any]] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def picker(self) -> Picker:
        return Picker(self.choices, self.fetch, self.read)

    def remember(self, key: tuple[str, ...], load: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self.lookups.get(key)
            if hit and time.monotonic() - hit[0] < TITLE_CACHE_SECONDS:
                self.lookups.move_to_end(key)
                return hit[1]
        value = load()
        with self._lock:
            self.lookups[key] = (time.monotonic(), value)
            self.lookups.move_to_end(key)
            while len(self.lookups) > LOOKUP_SIZE:
                self.lookups.popitem(last=False)
        return value

    def forget(self) -> None:
        if clear := getattr(self.fetch, "cache_clear", None):
            clear()
        now = time.monotonic()
        with self._lock:
            _drop_expired(self.lookups, now)
            _drop_expired(self.titles, now)

    def load(self, path: str) -> Image.Image:
        if path.startswith(overrides.FILE_PREFIX):
            return overrides.load(path)
        try:
            return self.fetch(path)
        except http.RequestError:
            if apple.is_apple(path):
                self.apple_down_until = time.monotonic() + APPLE_RETRY_SECONDS
                self.apple_dropped = True
            raise

    def retry_without_apple(self) -> bool:
        dropped, self.apple_dropped = self.apple_dropped, False
        return dropped

    def images(self, kind: Kind, tid: int) -> Images:
        images: Images = self.remember(("images", kind, str(tid)), lambda: self.tmdb.images(kind, tid))
        return images

    def fanart_images(self, kind: Kind, tid: int) -> FanartImages:
        """fanart.tv's images, or none while it is off. An outage raises, so the item fails and retries later."""
        fanart = self.fanart
        if fanart is None:
            return FanartImages()

        def load() -> FanartImages:
            return fanart.images(kind, tid, self.tmdb.tvdb_id(kind, tid) if kind == "tv" else None)

        images: FanartImages = self.remember(("fanart", kind, str(tid)), load)
        return images

    def apple_art(self, title: Title) -> str | None:
        region = self.settings.apple_region
        if region is None:
            return None
        key = f"apple:{title.kind}:{title.tmdb_id}:{region}"
        hit = self.choices.get_choice(key)
        today = self.settings.today
        if hit is not None and (today - date.fromisoformat(str(hit["checked"]))).days < APPLE_ART_DAYS:
            return str(hit["url"]) or None
        details = self.tmdb.details(title.kind, title.tmdb_id)
        try:
            url = apple.find(title.kind, details, region)
        except (http.RequestError, ValueError) as exc:
            known = (str(hit["url"]) or None) if hit is not None else None
            log.warning("Apple TV art lookup failed", extra={
                "title": title.name, "using": "last Apple TV art found" if known else "other art", "reason": str(exc),
            })  # fmt: skip
            return known
        self.choices.put_choice(key, {"url": url or "", "checked": today.isoformat()})
        return url

    def _apple_picked(self, title: Title, base: str) -> Picked | None:
        url = self.apple_art(title) if time.monotonic() >= self.apple_down_until else None
        if url is None:
            return None
        ref = ImageRef(url, None, 1680, 3636, 0.0, 0)
        try:
            return self.picker.textless(f"{base}:apple", [ref], title.all_titles)
        except http.RequestError as exc:
            log.warning(
                "Apple TV art not loaded", extra={"title": title.name, "using": "other art", "reason": str(exc)}
            )
            self.apple_down_until = time.monotonic() + APPLE_RETRY_SECONDS
            return None

    def art(self, title: Title) -> Picked | None:
        base = f"{title.kind}:{title.tmdb_id}"
        images = self.images(title.kind, title.tmdb_id)
        picked = (
            self.picker.textless(f"{base}:posters", images.textless_posters(), title.all_titles)
            or self._apple_picked(title, base)
            or self.picker.textless(f"{base}:backdrops", images.textless_backdrops(), title.all_titles)
        )
        if picked is None and self.fanart is not None:
            extra = self.fanart_images(title.kind, title.tmdb_id).images
            picked = self.picker.textless_art(f"{base}:fanart", extra, title.all_titles)
        return picked

    def logo(self, title: Title, *, dark: bool = False) -> str | None:
        base = f"{title.kind}:{title.tmdb_id}"
        languages, wordmark = self.settings.logo_languages, self.settings.prefer_wordmark
        path = self.picker.logo(base, self.images(title.kind, title.tmdb_id), languages, wordmark, dark=dark)
        if path is None and self.fanart is not None:
            extra = self.fanart_images(title.kind, title.tmdb_id).images
            path = self.picker.logo(f"{base}:fanart", extra, languages, wordmark, dark=dark)
        return path

    def backdrop(self, title: Title) -> Picked | None:
        base = f"{title.kind}:{title.tmdb_id}"
        picked = self.picker.background(base, self.images(title.kind, title.tmdb_id), title.all_titles)
        if picked is None and self.fanart is not None:
            extra = self.fanart_images(title.kind, title.tmdb_id).images
            picked = self.picker.background(f"{base}:fanart", extra, title.all_titles)
        return picked

    def season_images(self, tid: int, season: int) -> Images:
        key = ("season", str(tid), str(season))
        images: Images = self.remember(key, lambda: self.tmdb.season_images(tid, season))
        return images

    def details(self, kind: Kind, tid: int) -> Details:
        def load() -> Details:
            full: Mapping[str, object] = self.tmdb.details(kind, tid)
            return cast(Details, {name: full[name] for name in DETAIL_FIELDS if name in full})

        details: Details = self.remember(("details", kind, str(tid)), load)
        return details

    def find(self, kind: Kind, source: str, external_id: str) -> int | None:
        key = f"find:{source}:{external_id}"
        hit = self.choices.get_choice(key)
        if hit is None:
            found = self.remember(("find", source, external_id), lambda: self.tmdb.find(source, external_id))
            if found:
                self.choices.put_choice(key, found)
            hit = found
        value = hit.get(kind)
        return int(value) if value else None

    def service(self, providers: Mapping[str, RegionOffers]) -> str | None:
        """The first offer is the service. When its mark is left out, the poster has no mark."""
        for offer in services.offers(providers, self.settings.regions):
            if key := services.service_for(offer.name):
                return key
            return self.marks.get(offer, self.tmdb) if self.marks is not None else None
        return None

    def title(self, kind: Kind, tid: int, name: str) -> Title:
        with self._lock:
            hit = self.titles.get((kind, tid))
        if hit and time.monotonic() - hit[0] < TITLE_CACHE_SECONDS:
            return hit[1]
        every = list(dict.fromkeys([name, *self.tmdb.all_titles(kind, tid)]))
        service = self.service(self.tmdb.watch_providers(kind, tid)) if kind == "tv" else None
        details = self.tmdb.details(kind, tid)
        font = preferred_family(str(details.get("original_language") or ""), details.get("origin_country") or [])
        title = Title(kind, tid, name, every, service, font)
        with self._lock:
            self.titles[(kind, tid)] = (time.monotonic(), title)
        return title


def _drop_expired[K, V](cache: dict[K, tuple[float, V]], now: float) -> None:
    for key in [key for key, (stored, _) in cache.items() if now - stored >= TITLE_CACHE_SECONDS]:
        del cache[key]
