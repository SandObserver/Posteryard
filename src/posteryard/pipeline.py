import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from posteryard import apple, http, maintainerr, ocr, overrides, quality, services, similar, status
from posteryard.artwork import ChoiceCache, MemoryChoices, Picked, Picker
from posteryard.automarks import AutoMarks
from posteryard.config import EpisodeMode
from posteryard.fanart import Fanart, FanartImages, is_fanart
from posteryard.overrides import Override
from posteryard.quality import Badge, QualityMinimums
from posteryard.render import category, designs, lines
from posteryard.render.layers import NEAR_BLACK, WHITE, cover, family_for, trim
from posteryard.server import Item, MediaServer, Target, external_ids, tmdb_id
from posteryard.tmdb import ImageRef, Images, Kind, Tmdb, open_image

log = logging.getLogger(__name__)
TITLE_CACHE_SECONDS = 600
APPLE_ART_DAYS = 30
APPLE_RETRY_SECONDS = 3600
# Part of every fingerprint. Change it only when rendered output changes: every image is then re-rendered and
# re-uploaded. A release that renders the same images keeps it.
DESIGN_VERSION = "0.4.0"
SAME_PICTURE_BITS = 10
LOOKUP_SIZE = 4096
DETAIL_FIELDS = ("title", "name", "seasons", "next_episode_to_air")
# Downloaded images held in memory. Unbounded, a long-running service runs out of memory.
FETCH_CACHE = 8
# Thumbnails held in memory to compare pictures. They are not stored, to keep the database small.
THUMB_CACHE = 2000


class NotFoundError(Exception):
    pass


def fetch_art(path: str) -> Image.Image:
    if apple.is_apple(path):
        return open_image(http.request("GET", path, timeout=60, redirects=False))
    return Fanart.image(path) if is_fanart(path) else Tmdb.image(path)


@dataclass
class Plan:
    rating_key: str
    target: Target
    name: str
    inputs: dict[str, Any]
    draw: Callable[[], Image.Image]
    notes: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        blob = json.dumps({"version": DESIGN_VERSION, **self.inputs}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Title:
    kind: Kind
    tmdb_id: int
    name: str
    all_titles: list[str]
    service: str | None


@dataclass
class Context:
    tmdb: Tmdb
    minimums: QualityMinimums
    regions: tuple[str, ...]
    action_days: Mapping[str, date]
    today: date
    server: MediaServer | None = None
    labels: bool = True
    accessibility: frozenset[Badge] = frozenset()
    episodes: EpisodeMode = EpisodeMode.PLAIN
    logo_languages: tuple[str, ...] = ("en",)
    prefer_wordmark: bool = True
    choices: ChoiceCache = field(default_factory=MemoryChoices)
    read: Callable[[Image.Image], list[ocr.TextLine]] = ocr.read
    overrides: Callable[[str], Override | None] = field(default=lambda _key: None)
    fetch: Callable[[str], Image.Image] = field(default_factory=lambda: lru_cache(maxsize=FETCH_CACHE)(fetch_art))
    fanart: Fanart | None = None
    marks: AutoMarks | None = None
    apple_region: str | None = None
    titles: dict[tuple[Kind, int], tuple[float, Title]] = field(default_factory=dict)
    apple_down_until: float = 0.0
    apple_dropped: bool = False
    lookups: OrderedDict[tuple[str, ...], tuple[float, Any]] = field(default_factory=OrderedDict)
    thumbs: OrderedDict[str, similar.Thumb] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def picker(self) -> Picker:
        return Picker(self.choices, self.fetch, self.read)

    def leaving(self, *rating_keys: str) -> str | None:
        for key in rating_keys:
            days = maintainerr.days_left(self.action_days.get(key), self.today)
            if days is not None:
                return maintainerr.label(days)
        return None

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

    def _picture(self, path: str) -> Mapping[str, Any]:
        hit = self.choices.get_choice(f"poster-hash:{path}")
        if hit is not None and "histogram" in hit:
            return hit
        image = self.fetch(path)
        shown = cover(image, 90, 135)
        grey = np.asarray(shown.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
        bits = (grey[:, 1:] > grey[:, :-1]).flatten()
        value = int("".join("1" if b else "0" for b in bits), 2)
        hit = {"hash": value, "histogram": similar.histogram(image)}
        self.choices.put_choice(f"poster-hash:{path}", hit)
        return hit

    def thumb(self, path: str) -> similar.Thumb:
        with self._lock:
            hit = self.thumbs.get(path)
            if hit is not None:
                self.thumbs.move_to_end(path)
                return hit
        value = similar.thumb(self.fetch(path))
        with self._lock:
            self.thumbs[path] = value
            while len(self.thumbs) > THUMB_CACHE:
                self.thumbs.popitem(last=False)
        return value

    def same_picture(self, a: str, b: str, *, redrawn: bool = True) -> bool:
        first, second = self._picture(a), self._picture(b)
        if bin(int(first["hash"]) ^ int(second["hash"])).count("1") <= SAME_PICTURE_BITS:
            return True
        shared = similar.overlap(first["histogram"], second["histogram"])
        if shared < similar.HISTOGRAM_SAME:
            return False
        return similar.same_picture(self.thumb(a), self.thumb(b), shared, redrawn=redrawn)

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

    def apple_art(self, title: "Title") -> str | None:
        region = self.apple_region
        if region is None:
            return None
        key = f"apple:{title.kind}:{title.tmdb_id}:{region}"
        hit = self.choices.get_choice(key)
        if hit is not None and (self.today - date.fromisoformat(str(hit["checked"]))).days < APPLE_ART_DAYS:
            return str(hit["url"]) or None
        details = self.tmdb.details(title.kind, title.tmdb_id)
        try:
            url = apple.find(title.kind, details, region)
        except (http.RequestError, ValueError) as exc:
            known = (str(hit["url"]) or None) if hit is not None else None
            log.warning(
                "Apple TV art lookup for %s failed, using %s: %s",
                title.name, "the last Apple TV art found" if known else "other art", exc,
            )  # fmt: skip
            return known
        self.choices.put_choice(key, {"url": url or "", "checked": self.today.isoformat()})
        return url

    def art(self, title: "Title") -> Picked | None:
        base = f"{title.kind}:{title.tmdb_id}"
        url = self.apple_art(title) if time.monotonic() >= self.apple_down_until else None
        if url is not None:
            ref = ImageRef(url, None, 1680, 3636, 0.0, 0)
            try:
                picked = self.picker.textless(f"{base}:apple", [ref], title.all_titles)
            except http.RequestError as exc:
                log.warning("Apple TV art for %s could not be loaded, using other art: %s", title.name, exc)
                self.apple_down_until = time.monotonic() + APPLE_RETRY_SECONDS
                picked = None
            if picked is not None:
                return picked
        picked = self.picker.textless_art(base, self.images(title.kind, title.tmdb_id), title.all_titles)
        if picked is None and self.fanart is not None:
            extra = self.fanart_images(title.kind, title.tmdb_id).images
            picked = self.picker.textless_art(f"{base}:fanart", extra, title.all_titles)
        return picked

    def logo(self, title: "Title", *, dark: bool = False) -> str | None:
        base = f"{title.kind}:{title.tmdb_id}"
        images = self.images(title.kind, title.tmdb_id)
        path = self.picker.logo(base, images, self.logo_languages, self.prefer_wordmark, dark=dark)
        if path is None and self.fanart is not None:
            extra = self.fanart_images(title.kind, title.tmdb_id).images
            path = self.picker.logo(f"{base}:fanart", extra, self.logo_languages, self.prefer_wordmark, dark=dark)
        return path

    def fade(self, art: str, logo: str | None, name: str, below: list[lines.Line]) -> float:
        key = f"poster-fade:{art}:{logo or name}:{','.join(type(line).__name__ for line in below)}"
        hit = self.choices.get_choice(key)
        if hit is None:
            image = trim(self.fetch(logo)) if logo else designs.text_logo(name)
            hit = {"strength": designs.fade_strength(self.load(art), image, below)}
            self.choices.put_choice(key, hit)
        return float(hit["strength"])

    def corner_dark(self, path: str, area: str) -> bool:
        key = f"poster-corner:{path}:{area}"
        hit = self.choices.get_choice(key)
        if hit is None:
            hit = {"dark": designs.corner_dark(self.load(path), area)}
            self.choices.put_choice(key, hit)
        return bool(hit["dark"])

    def dark_reads(self, art: str, logo: str | None, name: str, below: list[lines.Line]) -> bool:
        key = f"poster-dark:{art}:{logo or name}:{','.join(type(line).__name__ for line in below)}"
        hit = self.choices.get_choice(key)
        if hit is None:
            image = trim(self.fetch(logo)) if logo else designs.text_logo(name)
            hit = {"reads": designs.dark_logo_reads(self.load(art), image, below)}
            self.choices.put_choice(key, hit)
        return bool(hit["reads"])

    def one_colour(self, logo: str) -> bool:
        hit = self.choices.get_choice(f"logo-colour:{logo}")
        if hit is None:
            hit = {"one": designs.one_colour(trim(self.fetch(logo)))}
            self.choices.put_choice(f"logo-colour:{logo}", hit)
        return bool(hit["one"])

    def backdrop(self, title: "Title") -> Picked | None:
        base = f"{title.kind}:{title.tmdb_id}"
        picked = self.picker.background(base, self.images(title.kind, title.tmdb_id), title.all_titles)
        if picked is None and self.fanart is not None:
            extra = self.fanart_images(title.kind, title.tmdb_id).images
            picked = self.picker.background(f"{base}:fanart", extra, title.all_titles)
        return picked

    def no_art(self, name: str) -> NotFoundError:
        sources = "TMDB and fanart.tv have" if self.fanart is not None else "TMDB has"
        return NotFoundError(f"{sources} no textless art for {name}")

    def season_images(self, tid: int, season: int) -> Images:
        key = ("season", str(tid), str(season))
        images: Images = self.remember(key, lambda: self.tmdb.season_images(tid, season))
        return images

    def details(self, kind: Kind, tid: int) -> Mapping[str, Any]:
        def load() -> Mapping[str, Any]:
            full = self.tmdb.details(kind, tid)
            return {name: full[name] for name in DETAIL_FIELDS if name in full}

        details: Mapping[str, Any] = self.remember(("details", kind, str(tid)), load)
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

    def service(self, providers: Mapping[str, Any]) -> str | None:
        for offer in services.offers(providers, self.regions):
            if key := services.service_for(offer.name):
                return key
            if self.marks is not None and (key := self.marks.get(offer, self.tmdb)):
                return key
        return None

    def title(self, kind: Kind, tid: int, name: str) -> Title:
        hit = self.titles.get((kind, tid))
        if hit and time.monotonic() - hit[0] < TITLE_CACHE_SECONDS:
            return hit[1]
        every = list(dict.fromkeys([name, *self.tmdb.all_titles(kind, tid)]))
        service = self.service(self.tmdb.watch_providers(kind, tid)) if kind == "tv" else None
        title = Title(kind, tid, name, every, service)
        self.titles[(kind, tid)] = (time.monotonic(), title)
        return title


def resolve_tmdb(ctx: Context, item: Item, kind: Kind) -> int | None:
    tid = tmdb_id(item)
    if tid is not None:
        return tid
    ids = external_ids(item)
    for source in ("imdb", "tvdb"):
        if source in ids and (found := ctx.find(kind, source, ids[source])) is not None:
            return found
    return None


def _require_tmdb(ctx: Context, item: Item, kind: Kind) -> int:
    tid = resolve_tmdb(ctx, item, kind)
    if tid is not None:
        return tid
    raise NotFoundError(
        f"{item.get('title')} ({item.get('ratingKey')}) has no TMDB id, and TMDB knows no IMDb or TVDB id of it"
    )


def _season_label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


def _season_art(ctx: Context, title: Title, season: int, show_art: str, numbers: Sequence[int]) -> tuple[str, str]:
    key = ("seasons", str(title.tmdb_id), *map(str, numbers))
    assignment: dict[int, tuple[str, str]] = ctx.remember(key, lambda: _assign_seasons(ctx, title, show_art, numbers))
    return assignment.get(season, (show_art, f"show art {show_art}"))


def _season_assignment_paths(
    ctx: Context, title: Title, show_art: str, siblings: Sequence[int], season: int | None
) -> list[str]:
    if season is None:
        return []
    key = ("seasons", str(title.tmdb_id), *map(str, siblings))
    assignment: dict[int, tuple[str, str]] = ctx.remember(key, lambda: _assign_seasons(ctx, title, show_art, siblings))
    return [show_art, *(path for number, (path, _) in assignment.items() if number != season)]


def _assign_seasons(ctx: Context, title: Title, show_art: str, numbers: Sequence[int]) -> dict[int, tuple[str, str]]:
    """TMDB stores one picture under several file names and crops, so "used" compares pictures, not names."""
    base = f"{title.kind}:{title.tmdb_id}"
    used = [show_art]
    assignment: dict[int, tuple[str, str]] = {}
    own: dict[int, str] = {}
    for number in numbers:
        refs = ctx.season_images(title.tmdb_id, number).textless_posters() or [
            r for r in ctx.fanart_images(title.kind, title.tmdb_id).seasons.get(number, []) if r.language is None
        ]
        picked = ctx.picker.textless(f"{base}:s{number}", refs, title.all_titles)
        if picked is None:
            continue
        own[number] = picked.path
        if not _seen(ctx, picked.path, used):
            used.append(picked.path)
            assignment[number] = (picked.path, f"season art {picked.path}")
    pool = ctx.picker.textless_all(
        f"{base}:pool", ctx.images(title.kind, title.tmdb_id).textless_art(), title.all_titles
    )
    reserved = [path for number, path in own.items() if number not in assignment]
    for number in numbers:
        if number in assignment:
            continue
        others = [path for other, path in own.items() if other != number and path not in used]
        choice = next((p for p in pool if not _seen(ctx, p, [*used, *others])), None)
        if choice is None and number in own and not _seen(ctx, own[number], used, redrawn=False):
            choice = own[number]
        if choice is None:
            choice = next((p for p in pool if not _seen(ctx, p, [*used, *reserved], redrawn=False)), None)
        if choice is not None:
            used.append(choice)
            assignment[number] = (
                choice,
                f"season art {choice}" if choice == own.get(number) else f"series art {choice}",
            )
    return assignment


def _next_unused(ctx: Context, title: Title, avoid: Sequence[str], skip: frozenset[str]) -> str | None:
    base = f"{title.kind}:{title.tmdb_id}"
    pool = ctx.picker.textless_all(
        f"{base}:pool", ctx.images(title.kind, title.tmdb_id).textless_art(), title.all_titles
    )
    used = [*avoid, *skip]
    return next((path for path in pool if not _seen(ctx, path, used)), None)


def _seen(ctx: Context, path: str, used: Sequence[str], *, redrawn: bool = True) -> bool:
    return any(path == other or ctx.same_picture(path, other, redrawn=redrawn) for other in used)


def _bottom_ink(
    ctx: Context, title: Title, art: str, logo: str | None, below: list[lines.Line]
) -> tuple[str | None, bool, bool]:
    """The logo to draw, whether it is dark with no fade, and whether a one-colour logo is drawn dark.

    A one-colour logo is drawn dark itself, so every poster of a title keeps one logo design.
    """
    if logo is None:
        return None, ctx.dark_reads(art, None, title.name, below), False
    if ctx.one_colour(logo):
        dark = ctx.dark_reads(art, logo, title.name, below)
        return logo, dark, dark
    dark_logo = ctx.logo(title, dark=True)
    if dark_logo is not None and ctx.dark_reads(art, dark_logo, title.name, below):
        return dark_logo, True, False
    return logo, False, False


def _drawn_with(text_logo: str | None, dark_bottom: bool, dark_corner: bool, fade: float) -> dict[str, Any]:
    """Plan inputs that differ from the default look, so unchanged posters keep their fingerprints."""
    drawn: dict[str, Any] = {}
    if text_logo is not None:
        drawn["text_logo"] = text_logo
        if (family := family_for(text_logo)) != "Inter":
            drawn["font"] = family
    if dark_bottom:
        drawn["ink"] = "dark"
    if dark_corner:
        drawn["corner"] = "dark"
    if fade != 1.0:
        drawn["fade"] = fade
    return drawn


def _poster(
    ctx: Context,
    title: Title,
    key: str,
    name: str,
    *,
    season: int | None,
    siblings: Sequence[int] = (),
    badges: list[quality.Badge],
    label: lines.Label | None,
    access: list[quality.Badge] | None = None,
) -> Plan:
    override = ctx.overrides(key)
    show_art = None if override and override.custom else ctx.art(title)
    logo = ctx.logo(title)
    extra: dict[str, Any] = {}
    if override and override.custom:
        art_path, note = overrides.FILE_PREFIX + override.custom, "custom art"
        extra["override"] = Path(override.custom).name
    elif show_art is None:
        raise ctx.no_art(name)
    else:
        if season is None:
            art_path, note = show_art.path, f"art {show_art.path}"
        else:
            art_path, note = _season_art(ctx, title, season, show_art.path, siblings)
        if override and override.skip:
            others = _season_assignment_paths(ctx, title, show_art.path, siblings, season)
            replacement = _next_unused(ctx, title, others, override.skip)
            if replacement is not None:
                art_path, note = replacement, f"next art {replacement}"
            else:
                note += ", no other art left to switch to"
            extra["override"] = sorted(override.skip)
    below: list[lines.Line] = []
    if season == 0:
        below.append(lines.Caption(_season_label(0)))
    if badges:
        below.append(lines.Badges(tuple(badges)))
    if access:
        below.append(lines.Badges(tuple(access)))
    number = season or None
    service = title.service if season is None else None
    logo_path, dark_bottom, recoloured = _bottom_ink(ctx, title, art_path, logo, below)
    corner = "mark" if service else "number" if number is not None else None
    dark_corner = corner is not None and ctx.corner_dark(art_path, corner)
    ink = NEAR_BLACK if dark_bottom else WHITE
    corner_ink = NEAR_BLACK if dark_corner else WHITE
    fade = 1.0 if dark_bottom else ctx.fade(art_path, logo_path, title.name, below)

    def draw() -> Image.Image:
        if logo_path is None:
            mark = designs.text_logo(title.name, ink)
        else:
            mark = trim(ctx.fetch(logo_path))
            mark = designs.recolour(mark, ink) if recoloured else mark
        return designs.tile_poster(
            ctx.load(art_path), mark, lines_below=below, label=label, number=number, service=service,
            ink=ink, corner_ink=corner_ink, fade=fade,
        )  # fmt: skip

    if recoloured:
        extra["logo_ink"] = "dark"
    if service and not dark_corner:
        extra["corner"] = "light"
    extra.update(_drawn_with(None if logo_path else title.name, dark_bottom, dark_corner, fade))
    inputs = {
        "design": "tile", "art": art_path, "logo": logo_path, "label": label, "lines": below,
        "number": number, "service": service, **extra,
    }  # fmt: skip
    return Plan(key, "poster", name, inputs, draw, [note])


def _background(ctx: Context, title: Title, key: str) -> list[Plan]:
    try:
        picked = ctx.backdrop(title)
    except http.RequestError as exc:
        log.warning("Background for %s skipped until the next pass: %s", title.name, exc)
        return []
    if picked is None:
        return []
    path = picked.path

    def draw() -> Image.Image:
        return designs.background(ctx.fetch(path))

    return [Plan(key, "art", title.name, {"design": "background", "art": path}, draw, [f"backdrop {path}"])]


def movie(ctx: Context, item: Item) -> list[Plan]:
    key = str(item["ratingKey"])
    title = ctx.title("movie", _require_tmdb(ctx, item, "movie"), str(item.get("title", "")))
    badges = quality.badges(quality.best(item.get("Media") or []), ctx.minimums)
    access = quality.accessibility(item.get("Media") or [], ctx.accessibility)
    label = _label(ctx, status.Dates(added=status.from_timestamp(item.get("addedAt"))), ctx.leaving(key))
    poster = _poster(ctx, title, key, title.name, season=None, badges=badges, label=label, access=access)
    return [poster, *_background(ctx, title, key)]


def show(ctx: Context, item: Item) -> list[Plan]:
    key = str(item["ratingKey"])
    title = ctx.title("tv", _require_tmdb(ctx, item, "tv"), str(item.get("title", "")))
    dates = status.Dates(
        added=status.from_timestamp(item.get("addedAt")),
        newest_season=_newest(ctx, item, "season", "show.id", key),
        newest_episode=_newest(ctx, item, "episode", "show.id", key),
        next_season=status.next_season(ctx.details("tv", title.tmdb_id)) if ctx.labels else None,
    )
    poster = _poster(ctx, title, key, title.name, season=None, badges=[], label=_label(ctx, dates, ctx.leaving(key)))
    return [poster, *_background(ctx, title, key)]


def season(ctx: Context, title: Title, item: Item, show_added: date | None = None) -> list[Plan]:
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    leaving = ctx.leaving(key, str(item.get("parentRatingKey", "")))
    dates = status.Dates(
        added=show_added,
        newest_season=status.from_timestamp(item.get("addedAt")),
        newest_episode=_newest(ctx, item, "episode", "season.id", key),
    )
    name = f"{title.name} · {_season_label(number)}"
    siblings = _sibling_seasons(ctx, title, item)
    label = _label(ctx, dates, leaving)
    return [_poster(ctx, title, key, name, season=number, siblings=siblings, badges=[], label=label)]


def _label(ctx: Context, dates: status.Dates, leaving: str | None) -> lines.Label | None:
    return status.label(dates if ctx.labels else status.Dates(), ctx.today, leaving)


def _newest(ctx: Context, item: Item, kind: str, field: str, key: str) -> date | None:
    section = str(item.get("librarySectionID", ""))
    if not ctx.labels or ctx.server is None or not section:
        return None
    server = ctx.server
    found = ctx.remember(("newest", kind, field, key), lambda: server.newest_added(section, kind, **{field: key}))
    return status.from_timestamp(found)


def _sibling_seasons(ctx: Context, title: Title, item: Item) -> list[int]:
    parent = str(item.get("parentRatingKey", ""))
    if ctx.server is not None and parent:
        children = ctx.remember(("children", parent), lambda: ctx.server.children(parent) if ctx.server else [])
        return sorted(int(child.get("index", 0)) for child in children)
    return sorted(int(s["season_number"]) for s in ctx.details("tv", title.tmdb_id).get("seasons") or [])


def episode(ctx: Context, title: Title, item: Item) -> list[Plan]:
    if ctx.episodes == EpisodeMode.OFF:
        return []
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    season_number, name = int(item.get("parentIndex", 0)), str(item.get("title", ""))
    still = (ctx.tmdb.episode(title.tmdb_id, season_number, number) or {}).get("still_path")
    if not still:
        return []
    path = str(still)

    titled = ctx.episodes == EpisodeMode.TITLED

    def draw() -> Image.Image:
        return designs.episode_still(ctx.fetch(path), number, name if titled else None)

    inputs = {
        "design": "episode",
        "still": path,
        **({"number": number, "title": name} if titled else {"mode": "plain"}),
        **({"font": family} if titled and (family := family_for(name)) != "Inter" else {}),
    }
    return [Plan(key, "thumb", f"{title.name} · S{season_number} E{number} · {name}", inputs, draw, [f"still {path}"])]


def collection(ctx: Context, item: Item) -> list[Plan]:
    if ctx.server is None:
        raise NotFoundError("No media server is configured")
    key, name = str(item["ratingKey"]), str(item.get("title", ""))
    members = [
        (m, tid)
        for m in ctx.server.collection_children(key)
        if m.get("type") in ("movie", "show")
        and (tid := resolve_tmdb(ctx, m, "movie" if m.get("type") == "movie" else "tv")) is not None
    ]
    if not members:
        return []
    featured, featured_id = max(members, key=lambda pair: int(pair[0].get("addedAt") or 0))
    kind: Kind = "movie" if featured.get("type") == "movie" else "tv"
    title = ctx.title(kind, featured_id, str(featured.get("title", "")))
    art = ctx.art(title)
    if art is None:
        raise ctx.no_art(f"{title.name}, the newest title in {name}")
    art_path = art.path
    service = services.service_for(name)
    if service:
        logo = ctx.logo(title)

        def channel() -> Image.Image:
            featured_logo = trim(ctx.fetch(logo)) if logo else designs.text_logo(title.name)
            return designs.channel_tile(ctx.fetch(art_path), featured_logo, service)

        inputs = {"design": "channel", "art": art_path, "logo": logo, "featured": title.name, "service": service}
        return [Plan(key, "poster", name, inputs, channel, [f"art {art_path} from {title.name}"])]

    def tile() -> Image.Image:
        return category.category_tile(ctx.fetch(art_path), name)

    inputs = {"design": "category", "art": art_path, "title": name, "palette": category.palette_for(name)}
    return [Plan(key, "poster", name, inputs, tile, [f"art {art_path} from {title.name}"])]


def _show(ctx: Context, item: Item) -> tuple[Title, Item]:
    if ctx.server is None:
        raise NotFoundError("No media server is configured")
    show_key = str(item["parentRatingKey"] if item.get("type") == "season" else item["grandparentRatingKey"])
    show_item = ctx.server.item(show_key)
    if show_item is None:
        raise NotFoundError(f"{ctx.server.name} has no show {show_key}")
    return ctx.title("tv", _require_tmdb(ctx, show_item, "tv"), str(show_item.get("title", ""))), show_item


def plan_item(ctx: Context, item: Item) -> list[Plan]:
    kind = item.get("type")
    if kind == "movie":
        return movie(ctx, item)
    if kind == "show":
        return show(ctx, item)
    if kind == "season":
        title, show_item = _show(ctx, item)
        return season(ctx, title, item, status.from_timestamp(show_item.get("addedAt")))
    if kind == "episode":
        return episode(ctx, _show(ctx, item)[0], item)
    if kind == "collection":
        return collection(ctx, item)
    return []


def plan_preview(ctx: Context, rating_key: str, episodes: int | None = 0) -> list[Plan]:
    if ctx.server is None:
        raise NotFoundError("No media server is configured")
    item = ctx.server.item(rating_key)
    if item is None:
        raise NotFoundError(f"{ctx.server.name} has no item {rating_key}")
    plans = plan_item(ctx, item)
    kind = item.get("type")
    seasons = ctx.server.children(rating_key) if kind == "show" else [item] if kind == "season" else []
    for season_item in seasons:
        if season_item is not item:
            plans += plan_item(ctx, season_item)
        if episodes != 0:
            for episode_item in ctx.server.children(str(season_item["ratingKey"]))[:episodes]:
                plans += plan_item(ctx, episode_item)
    return plans


def plan_tmdb(ctx: Context, kind: Kind, tid: int, seasons: Sequence[int] = ()) -> list[Plan]:
    details = ctx.tmdb.details(kind, tid)
    name = str(details.get("title") or details.get("name") or tid)
    item: Item = {"ratingKey": f"tmdb-{kind}-{tid}", "title": name, "Guid": [{"id": f"tmdb://{tid}"}]}
    if kind == "movie":
        return movie(ctx, item)
    plans = show(ctx, item)
    title = ctx.title("tv", tid, name)
    for number in seasons:
        plans += season(ctx, title, {"ratingKey": f"tmdb-tv-{tid}-s{number}", "index": number})
    return plans
