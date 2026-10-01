"""Turn one Plex item into finished images."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Any, Literal

from PIL import Image

from posteryard import artwork, maintainerr, ocr, quality, services
from posteryard.plex import Item, Plex, tmdb_id
from posteryard.quality import Badge, QualityMinimums
from posteryard.render import designs
from posteryard.tmdb import ImageRef, Kind, Tmdb

Target = Literal["poster", "art", "thumb"]


@dataclass
class Output:
    rating_key: str
    target: Target
    name: str
    image: Image.Image
    notes: list[str] = field(default_factory=list)


class NotFoundError(Exception):
    pass


@dataclass
class Context:
    tmdb: Tmdb
    minimums: QualityMinimums
    regions: tuple[str, ...]
    action_days: Mapping[str, date]
    today: date
    plex: Plex | None = None
    read: Callable[[Image.Image], list[ocr.TextLine]] = ocr.read
    fetch: Callable[[str], Image.Image] = field(default_factory=lambda: cache(Tmdb.image))

    def leaving(self, rating_key: str) -> str | None:
        days = maintainerr.days_left(self.action_days.get(rating_key), self.today)
        return maintainerr.label(days) if days is not None else None


@dataclass(frozen=True)
class Title:
    kind: Kind
    tmdb_id: int
    name: str
    english_titles: list[str]
    all_titles: list[str]
    service: str | None


def _title(ctx: Context, kind: Kind, tid: int, name: str) -> Title:
    english = list(dict.fromkeys([name, *ctx.tmdb.english_titles(kind, tid)]))
    every = list(dict.fromkeys([name, *ctx.tmdb.all_titles(kind, tid)]))
    service = services.pick(ctx.tmdb.watch_providers(kind, tid), ctx.regions) if kind == "tv" else None
    return Title(kind, tid, name, english, every, service)


def _fallback(
    ctx: Context, title: Title, *, caption: str | None, badges: list[Badge], leaving: str | None
) -> tuple[Image.Image, list[str]]:
    images = ctx.tmdb.images(title.kind, title.tmdb_id)
    art = artwork.textless_art(images, title.all_titles, ctx.fetch, ctx.read)
    logo = artwork.logo(images, ctx.fetch)
    if art is None or logo is None:
        raise NotFoundError(f"TMDB has no textless art or title logo for {title.name}")
    image = designs.fallback_poster(
        art.image, logo, caption=caption, badges=badges, leaving=leaving, service=title.service
    )
    return image, [f"fallback design, art {art.ref.path}"]


def _poster(
    ctx: Context, title: Title, key: str, refs: Sequence[ImageRef], *, badges: list[Badge], leaving: str | None
) -> Output:
    choice = artwork.titled_poster(refs, title.english_titles, ctx.fetch, ctx.read)
    if choice is not None:
        image = designs.studio_poster(choice.image, badges, leaving, title.service)
        return Output(key, "poster", title.name, image, [f"studio poster {choice.ref.path}"])
    image, notes = _fallback(ctx, title, caption=None, badges=badges, leaving=leaving)
    return Output(key, "poster", title.name, image, ["no English poster with a readable title", *notes])


def _background(ctx: Context, title: Title, key: str) -> list[Output]:
    images = ctx.tmdb.images(title.kind, title.tmdb_id)
    choice = artwork.background(images, title.all_titles, ctx.fetch, ctx.read)
    if choice is None:
        return []
    return [Output(key, "art", title.name, designs.background(choice.image), [f"backdrop {choice.ref.path}"])]


def movie(ctx: Context, item: Item) -> list[Output]:
    key = str(item["ratingKey"])
    title = _title(ctx, "movie", _require_tmdb(item), str(item.get("title", "")))
    badges = quality.badges(quality.best(item.get("Media") or []), ctx.minimums)
    refs = ctx.tmdb.images("movie", title.tmdb_id).english_posters()
    return [_poster(ctx, title, key, refs, badges=badges, leaving=ctx.leaving(key)), *_background(ctx, title, key)]


def show(ctx: Context, item: Item, episodes: int | None = 0) -> list[Output]:
    key = str(item["ratingKey"])
    title = _title(ctx, "tv", _require_tmdb(item), str(item.get("title", "")))
    refs = ctx.tmdb.images("tv", title.tmdb_id).english_posters()
    out = [_poster(ctx, title, key, refs, badges=[], leaving=ctx.leaving(key)), *_background(ctx, title, key)]
    if ctx.plex is not None:
        for season_item in ctx.plex.children(key):
            out += season(ctx, title, season_item, episodes)
    return out


def season(ctx: Context, title: Title, item: Item, episodes: int | None = 0) -> list[Output]:
    """`episodes` is how many episodes to render: 0 for none, None for all."""
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    leaving = ctx.leaving(key)
    refs = ctx.tmdb.season_images(title.tmdb_id, number).english_posters()
    choice = artwork.titled_poster(refs, title.english_titles, ctx.fetch, ctx.read)
    if choice is not None:
        printed = ocr.mentions_season(choice.lines, number)
        image = designs.season_poster(choice.image, None if printed else number, leaving, title.service)
        notes = [f"season poster {choice.ref.path}" + (", number already printed" if printed else "")]
    else:
        caption = "Specials" if number == 0 else f"Season {number}"
        image, notes = _fallback(ctx, title, caption=caption, badges=[], leaving=leaving)
    out = [Output(key, "poster", f"{title.name} · Season {number}", image, notes)]
    if episodes != 0 and ctx.plex is not None:
        for episode_item in ctx.plex.children(key)[:episodes]:
            out += episode(ctx, title, number, episode_item)
    return out


def episode(ctx: Context, title: Title, season_number: int, item: Item) -> list[Output]:
    key, number, name = str(item["ratingKey"]), int(item.get("index", 0)), str(item.get("title", ""))
    still = (ctx.tmdb.episode(title.tmdb_id, season_number, number) or {}).get("still_path")
    if not still:
        return []
    image = designs.episode_still(ctx.fetch(str(still)), number, name)
    return [Output(key, "thumb", f"{title.name} · S{season_number} E{number} · {name}", image, [f"still {still}"])]


def _require_tmdb(item: Item) -> int:
    tid = tmdb_id(item)
    if tid is None:
        raise NotFoundError(f"{item.get('title')} ({item.get('ratingKey')}) has no TMDB id in Plex")
    return tid


def render_plex(ctx: Context, rating_key: str, episodes: int | None = 0) -> list[Output]:
    if ctx.plex is None:
        raise NotFoundError("Plex is not configured")
    item = ctx.plex.item(rating_key)
    if item is None:
        raise NotFoundError(f"Plex has no item {rating_key}")
    kind = item.get("type")
    if kind == "movie":
        return movie(ctx, item)
    if kind == "show":
        return show(ctx, item, episodes)
    if kind not in ("season", "episode"):
        raise NotFoundError(f"Plex item {rating_key} is a {kind}, not a movie, show, season or episode")
    show_key = str(item["parentRatingKey"] if kind == "season" else item["grandparentRatingKey"])
    show_item = ctx.plex.item(show_key)
    if show_item is None:
        raise NotFoundError(f"Plex has no show {show_key}")
    title = _title(ctx, "tv", _require_tmdb(show_item), str(show_item.get("title", "")))
    if kind == "episode":
        return episode(ctx, title, int(item.get("parentIndex", 0)), item)
    return season(ctx, title, item, episodes)


def render_tmdb(ctx: Context, kind: Kind, tid: int, seasons: Sequence[int] = ()) -> list[Output]:
    """Render from TMDB alone, without Plex: no badges, labels or episodes."""
    details = ctx.tmdb.details(kind, tid)
    name = str(details.get("title") or details.get("name") or tid)
    item: dict[str, Any] = {"ratingKey": f"tmdb-{kind}-{tid}", "title": name, "Guid": [{"id": f"tmdb://{tid}"}]}
    if kind == "movie":
        return movie(ctx, item)
    out = show(ctx, item)
    title = _title(ctx, "tv", tid, name)
    for number in seasons:
        out += season(ctx, title, {"ratingKey": f"tmdb-tv-{tid}-s{number}", "index": number})
    return out
