"""Plan the images for one Plex item. A plan knows what decides the image before anything is drawn."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from posteryard import maintainerr, ocr, overrides, quality, services
from posteryard.artwork import ChoiceCache, MemoryChoices, Picker
from posteryard.overrides import Override
from posteryard.plex import Item, Plex, Target, tmdb_id
from posteryard.quality import QualityMinimums
from posteryard.render import designs
from posteryard.render.layers import cover, trim
from posteryard.tmdb import Images, Kind, Tmdb

TITLE_CACHE_SECONDS = 600
# Part of every fingerprint. Change it only when rendered output changes: every image is then re-rendered and
# re-uploaded. A release that renders the same images keeps it.
DESIGN_VERSION = "0.2.1"
# Perceptual hashes this close are the same picture at another size or crop.
SAME_PICTURE_BITS = 10
# Downloaded images held in memory. Unbounded, a long-running service runs out of memory.
FETCH_CACHE = 8


class NotFoundError(Exception):
    pass


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
    plex: Plex | None = None
    choices: ChoiceCache = field(default_factory=MemoryChoices)
    read: Callable[[Image.Image], list[ocr.TextLine]] = ocr.read
    overrides: Callable[[str], Override | None] = field(default=lambda _key: None)
    fetch: Callable[[str], Image.Image] = field(default_factory=lambda: lru_cache(maxsize=FETCH_CACHE)(Tmdb.image))
    titles: dict[tuple[Kind, int], tuple[float, Title]] = field(default_factory=dict)
    lookups: dict[tuple[str, ...], tuple[float, Any]] = field(default_factory=dict)

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
        hit = self.lookups.get(key)
        if hit and time.monotonic() - hit[0] < TITLE_CACHE_SECONDS:
            return hit[1]
        value = load()
        self.lookups[key] = (time.monotonic(), value)
        return value

    def load(self, path: str) -> Image.Image:
        """TMDB art by path, or the user's custom art by its `file:` path."""
        return overrides.load(path) if path.startswith(overrides.FILE_PREFIX) else self.fetch(path)

    def image_hash(self, path: str) -> int:
        """A 64-bit difference hash of the picture as a poster crop shows it, cached with the art choices."""
        hit = self.choices.get_choice(f"poster-hash:{path}")
        if hit is not None:
            return int(hit["hash"])
        shown = cover(self.fetch(path), 90, 135)
        grey = np.asarray(shown.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
        bits = (grey[:, 1:] > grey[:, :-1]).flatten()
        value = int("".join("1" if b else "0" for b in bits), 2)
        self.choices.put_choice(f"poster-hash:{path}", {"hash": value})
        return value

    def images(self, kind: Kind, tid: int) -> Images:
        images: Images = self.remember(("images", kind, str(tid)), lambda: self.tmdb.images(kind, tid))
        return images

    def season_images(self, tid: int, season: int) -> Images:
        key = ("season", str(tid), str(season))
        images: Images = self.remember(key, lambda: self.tmdb.season_images(tid, season))
        return images

    def details(self, kind: Kind, tid: int) -> Mapping[str, Any]:
        details: Mapping[str, Any] = self.remember(("details", kind, str(tid)), lambda: self.tmdb.details(kind, tid))
        return details

    def title(self, kind: Kind, tid: int, name: str) -> Title:
        hit = self.titles.get((kind, tid))
        if hit and time.monotonic() - hit[0] < TITLE_CACHE_SECONDS:
            return hit[1]
        every = list(dict.fromkeys([name, *self.tmdb.all_titles(kind, tid)]))
        service = services.pick(self.tmdb.watch_providers(kind, tid), self.regions) if kind == "tv" else None
        title = Title(kind, tid, name, every, service)
        self.titles[(kind, tid)] = (time.monotonic(), title)
        return title


def _require_tmdb(item: Item) -> int:
    tid = tmdb_id(item)
    if tid is None:
        raise NotFoundError(f"{item.get('title')} ({item.get('ratingKey')}) has no TMDB id in Plex")
    return tid


def _season_label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


def _season_art(ctx: Context, title: Title, season: int, show_art: str, numbers: Sequence[int]) -> tuple[str, str]:
    """Look up this season in the show's assignment. See `_assign_seasons`."""
    key = ("seasons", str(title.tmdb_id), *map(str, numbers))
    assignment: dict[int, tuple[str, str]] = ctx.remember(key, lambda: _assign_seasons(ctx, title, show_art, numbers))
    return assignment.get(season, (show_art, f"show art {show_art}"))


def _season_assignment_paths(
    ctx: Context, title: Title, show_art: str, siblings: Sequence[int], season: int | None
) -> list[str]:
    """Art the show poster and the other seasons use, which a replacement must not repeat."""
    if season is None:
        return []
    key = ("seasons", str(title.tmdb_id), *map(str, siblings))
    assignment: dict[int, tuple[str, str]] = ctx.remember(key, lambda: _assign_seasons(ctx, title, show_art, siblings))
    return [show_art, *(path for number, (path, _) in assignment.items() if number != season)]


def _assign_seasons(ctx: Context, title: Title, show_art: str, numbers: Sequence[int]) -> dict[int, tuple[str, str]]:
    """Each season gets its own textless art, else a series image nothing else uses, else the show's art.

    TMDB stores the same picture under several file names, so "used" compares pictures, not names.
    """
    base = f"{title.kind}:{title.tmdb_id}"
    used = [ctx.image_hash(show_art)]
    assignment: dict[int, tuple[str, str]] = {}
    for number in numbers:
        refs = ctx.season_images(title.tmdb_id, number).textless_posters()
        picked = ctx.picker.textless(f"{base}:s{number}", refs, title.all_titles)
        if picked and not _seen(ctx.image_hash(picked.path), used):
            used.append(ctx.image_hash(picked.path))
            assignment[number] = (picked.path, f"season art {picked.path}")
    pool = ctx.picker.textless_all(
        f"{base}:pool", ctx.images(title.kind, title.tmdb_id).textless_art(), title.all_titles
    )
    for number in numbers:
        if number in assignment:
            continue
        for path in pool:
            fingerprint = ctx.image_hash(path)
            if not _seen(fingerprint, used):
                used.append(fingerprint)
                assignment[number] = (path, f"series art {path}")
                break
    return assignment


def _next_unused(ctx: Context, title: Title, avoid: Sequence[str], skip: frozenset[str]) -> str | None:
    """The best series image that is none of the pictures in `avoid` or `skip`."""
    base = f"{title.kind}:{title.tmdb_id}"
    pool = ctx.picker.textless_all(
        f"{base}:pool", ctx.images(title.kind, title.tmdb_id).textless_art(), title.all_titles
    )
    used = [ctx.image_hash(path) for path in (*avoid, *skip)]
    return next((path for path in pool if not _seen(ctx.image_hash(path), used)), None)


def _seen(fingerprint: int, used: list[int]) -> bool:
    return any(bin(fingerprint ^ other).count("1") <= SAME_PICTURE_BITS for other in used)


def _poster(
    ctx: Context,
    title: Title,
    key: str,
    name: str,
    *,
    season: int | None,
    siblings: Sequence[int] = (),
    badges: list[quality.Badge],
    leaving: str | None,
) -> Plan:
    images = ctx.images(title.kind, title.tmdb_id)
    base = f"{title.kind}:{title.tmdb_id}"
    show_art = ctx.picker.textless_art(base, images, title.all_titles)
    logo = ctx.picker.logo(base, images)
    if show_art is None or logo is None:
        raise NotFoundError(f"TMDB has no textless art or title logo for {name}")
    if season is None:
        art_path, note = show_art.path, f"art {show_art.path}"
    else:
        art_path, note = _season_art(ctx, title, season, show_art.path, siblings)
    override = ctx.overrides(key)
    extra: dict[str, Any] = {}
    if override and override.custom:
        art_path, note = overrides.FILE_PREFIX + override.custom, "custom art"
        extra["override"] = Path(override.custom).name
    elif override and override.skip:
        others = _season_assignment_paths(ctx, title, show_art.path, siblings, season)
        replacement = _next_unused(ctx, title, others, override.skip)
        if replacement is not None:
            art_path, note = replacement, f"next art {replacement}"
        else:
            note += ", no other art left to switch to"
        extra["override"] = sorted(override.skip)
    label = None if season is None else _season_label(season)
    logo_path = logo

    def draw() -> Image.Image:
        return designs.tile_poster(
            ctx.load(art_path),
            trim(ctx.fetch(logo_path)),
            caption=label,
            badges=badges,
            leaving=leaving,
            service=title.service,
        )

    inputs = {
        "design": "tile", "art": art_path, "logo": logo_path, "label": label,
        "badges": badges, "leaving": leaving, "service": title.service, **extra,
    }  # fmt: skip
    return Plan(key, "poster", name, inputs, draw, [note])


def _background(ctx: Context, title: Title, key: str) -> list[Plan]:
    images = ctx.images(title.kind, title.tmdb_id)
    picked = ctx.picker.background(f"{title.kind}:{title.tmdb_id}", images, title.all_titles)
    if picked is None:
        return []
    path = picked.path

    def draw() -> Image.Image:
        return designs.background(ctx.fetch(path))

    return [Plan(key, "art", title.name, {"design": "background", "art": path}, draw, [f"backdrop {path}"])]


def movie(ctx: Context, item: Item) -> list[Plan]:
    key = str(item["ratingKey"])
    title = ctx.title("movie", _require_tmdb(item), str(item.get("title", "")))
    badges = quality.badges(quality.best(item.get("Media") or []), ctx.minimums)
    poster = _poster(ctx, title, key, title.name, season=None, badges=badges, leaving=ctx.leaving(key))
    return [poster, *_background(ctx, title, key)]


def show(ctx: Context, item: Item) -> list[Plan]:
    key = str(item["ratingKey"])
    title = ctx.title("tv", _require_tmdb(item), str(item.get("title", "")))
    poster = _poster(ctx, title, key, title.name, season=None, badges=[], leaving=ctx.leaving(key))
    return [poster, *_background(ctx, title, key)]


def season(ctx: Context, title: Title, item: Item) -> list[Plan]:
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    leaving = ctx.leaving(key, str(item.get("parentRatingKey", "")))
    name = f"{title.name} · {_season_label(number)}"
    siblings = _sibling_seasons(ctx, title, item)
    return [_poster(ctx, title, key, name, season=number, siblings=siblings, badges=[], leaving=leaving)]


def _sibling_seasons(ctx: Context, title: Title, item: Item) -> list[int]:
    """The seasons that share the show's images: the ones in Plex, or TMDB's list without Plex."""
    parent = str(item.get("parentRatingKey", ""))
    if ctx.plex is not None and parent.isdigit():
        children = ctx.remember(("children", parent), lambda: ctx.plex.children(parent) if ctx.plex else [])
        return sorted(int(child.get("index", 0)) for child in children)
    return sorted(int(s["season_number"]) for s in ctx.details("tv", title.tmdb_id).get("seasons") or [])


def episode(ctx: Context, title: Title, item: Item) -> list[Plan]:
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    season_number, name = int(item.get("parentIndex", 0)), str(item.get("title", ""))
    still = (ctx.tmdb.episode(title.tmdb_id, season_number, number) or {}).get("still_path")
    if not still:
        return []
    path = str(still)

    def draw() -> Image.Image:
        return designs.episode_still(ctx.fetch(path), number, name)

    inputs = {"design": "episode", "still": path, "number": number, "title": name}
    return [Plan(key, "thumb", f"{title.name} · S{season_number} E{number} · {name}", inputs, draw, [f"still {path}"])]


def _show_title(ctx: Context, item: Item) -> Title:
    if ctx.plex is None:
        raise NotFoundError("Plex is not configured")
    show_key = str(item["parentRatingKey"] if item.get("type") == "season" else item["grandparentRatingKey"])
    show_item = ctx.plex.item(show_key)
    if show_item is None:
        raise NotFoundError(f"Plex has no show {show_key}")
    return ctx.title("tv", _require_tmdb(show_item), str(show_item.get("title", "")))


def plan_item(ctx: Context, item: Item) -> list[Plan]:
    """Plans for one Plex item alone. A show's seasons and episodes are items of their own."""
    kind = item.get("type")
    if kind == "movie":
        return movie(ctx, item)
    if kind == "show":
        return show(ctx, item)
    if kind == "season":
        return season(ctx, _show_title(ctx, item), item)
    if kind == "episode":
        return episode(ctx, _show_title(ctx, item), item)
    return []


def plan_preview(ctx: Context, rating_key: str, episodes: int | None = 0) -> list[Plan]:
    """The item, and for a show or season its seasons and first `episodes` episodes. None means all episodes."""
    if ctx.plex is None:
        raise NotFoundError("Plex is not configured")
    item = ctx.plex.item(rating_key)
    if item is None:
        raise NotFoundError(f"Plex has no item {rating_key}")
    plans = plan_item(ctx, item)
    kind = item.get("type")
    seasons = ctx.plex.children(rating_key) if kind == "show" else [item] if kind == "season" else []
    for season_item in seasons:
        if season_item is not item:
            plans += plan_item(ctx, season_item)
        if episodes != 0:
            for episode_item in ctx.plex.children(str(season_item["ratingKey"]))[:episodes]:
                plans += plan_item(ctx, episode_item)
    return plans


def plan_tmdb(ctx: Context, kind: Kind, tid: int, seasons: Sequence[int] = ()) -> list[Plan]:
    """Plans from TMDB alone, without Plex: no badges, labels or episodes."""
    details = ctx.tmdb.details(kind, tid)
    name = str(details.get("title") or details.get("name") or tid)
    item: dict[str, Any] = {"ratingKey": f"tmdb-{kind}-{tid}", "title": name, "Guid": [{"id": f"tmdb://{tid}"}]}
    if kind == "movie":
        return movie(ctx, item)
    plans = show(ctx, item)
    title = ctx.title("tv", tid, name)
    for number in seasons:
        plans += season(ctx, title, {"ratingKey": f"tmdb-tv-{tid}-s{number}", "index": number})
    return plans
