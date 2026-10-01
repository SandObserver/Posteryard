"""Plan the images for one Plex item. A plan knows what decides the image before anything is drawn."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Any, Literal

from PIL import Image

from posteryard import __version__, maintainerr, ocr, quality, services
from posteryard.artwork import ChoiceCache, MemoryChoices, Picker
from posteryard.plex import Item, Plex, tmdb_id
from posteryard.quality import QualityMinimums
from posteryard.render import designs
from posteryard.render.layers import trim
from posteryard.tmdb import Kind, Tmdb

Target = Literal["poster", "art", "thumb"]
TITLE_CACHE_SECONDS = 600


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
        blob = json.dumps({"version": __version__, **self.inputs}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Title:
    kind: Kind
    tmdb_id: int
    name: str
    english_titles: list[str]
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
    fetch: Callable[[str], Image.Image] = field(default_factory=lambda: cache(Tmdb.image))
    titles: dict[tuple[Kind, int], tuple[float, Title]] = field(default_factory=dict)

    @property
    def picker(self) -> Picker:
        return Picker(self.choices, self.fetch, self.read)

    def leaving(self, *rating_keys: str) -> str | None:
        for key in rating_keys:
            days = maintainerr.days_left(self.action_days.get(key), self.today)
            if days is not None:
                return maintainerr.label(days)
        return None

    def title(self, kind: Kind, tid: int, name: str) -> Title:
        hit = self.titles.get((kind, tid))
        if hit and time.monotonic() - hit[0] < TITLE_CACHE_SECONDS:
            return hit[1]
        english = list(dict.fromkeys([name, *self.tmdb.english_titles(kind, tid)]))
        every = list(dict.fromkeys([name, *self.tmdb.all_titles(kind, tid)]))
        service = services.pick(self.tmdb.watch_providers(kind, tid), self.regions) if kind == "tv" else None
        title = Title(kind, tid, name, english, every, service)
        self.titles[(kind, tid)] = (time.monotonic(), title)
        return title


def _require_tmdb(item: Item) -> int:
    tid = tmdb_id(item)
    if tid is None:
        raise NotFoundError(f"{item.get('title')} ({item.get('ratingKey')}) has no TMDB id in Plex")
    return tid


def _season_label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


def _poster(
    ctx: Context,
    title: Title,
    key: str,
    name: str,
    *,
    season: int | None,
    badges: list[quality.Badge],
    leaving: str | None,
) -> Plan:
    if season is None:
        refs = ctx.tmdb.images(title.kind, title.tmdb_id).english_posters()
        cache_key = f"{title.kind}:{title.tmdb_id}"
    else:
        refs = ctx.tmdb.season_images(title.tmdb_id, season).english_posters()
        cache_key = f"{title.kind}:{title.tmdb_id}:s{season}"
    picked = ctx.picker.titled(cache_key, refs, title.english_titles)
    label = None if season is None else _season_label(season)
    common: dict[str, Any] = {"badges": badges, "leaving": leaving, "service": title.service}

    if picked is not None:
        printed = season is not None and ocr.mentions_season(picked.lines, season)
        shown = None if printed else label
        path = picked.path
        notes = [f"{'studio' if season is None else 'season'} poster {path}"]
        if printed:
            notes.append("season already printed")

        def draw() -> Image.Image:
            if season is None:
                return designs.studio_poster(ctx.fetch(path), badges, leaving, title.service)
            return designs.season_poster(ctx.fetch(path), shown, leaving, title.service)

        return Plan(key, "poster", name, {**common, "design": "studio", "art": path, "label": shown}, draw, notes)

    images = ctx.tmdb.images(title.kind, title.tmdb_id)
    base = f"{title.kind}:{title.tmdb_id}"
    art = ctx.picker.textless_art(base, images, title.all_titles)
    logo = ctx.picker.logo(base, images)
    if art is None or logo is None:
        raise NotFoundError(f"TMDB has no usable poster, textless art or title logo for {name}")
    art_path, logo_path = art.path, logo

    def draw_fallback() -> Image.Image:
        return designs.fallback_poster(
            ctx.fetch(art_path),
            trim(ctx.fetch(logo_path)),
            caption=label,
            badges=badges,
            leaving=leaving,
            service=title.service,
        )

    inputs = {**common, "design": "fallback", "art": art_path, "logo": logo_path, "label": label}
    notes = ["no English poster with a readable title", f"fallback design, art {art_path}"]
    return Plan(key, "poster", name, inputs, draw_fallback, notes)


def _background(ctx: Context, title: Title, key: str) -> list[Plan]:
    images = ctx.tmdb.images(title.kind, title.tmdb_id)
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
    return [_poster(ctx, title, key, name, season=number, badges=[], leaving=leaving)]


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
