import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image

from posteryard import apple, http, overrides, quality, services, status
from posteryard.artwork import (
    LOGO_RULE,
    MAX_CANDIDATES,
    POOL_SIZE,
    TEXTLESS_RULE,
)
from posteryard.config import EpisodeMode
from posteryard.fanart import is_fanart
from posteryard.measures import Measures
from posteryard.render import category, designs, lines
from posteryard.render.layers import NEAR_BLACK, WHITE, family_for, trim
from posteryard.server import Item, MediaServer, Target, external_ids, tmdb_id
from posteryard.settings import Settings
from posteryard.sources import Sources, Title
from posteryard.tmdb import ImageRef, Kind

log = logging.getLogger(__name__)
# Part of every fingerprint. Change it only when rendered output changes: every image is then re-rendered and
# re-uploaded. A release that renders the same images keeps it.
DESIGN_VERSION = "0.4.0"
MEASURED = f"measured:{DESIGN_VERSION}:"
# Each cache key family, with the prefixes still in use. Rows of a family under any other prefix are deleted at start.
CHOICE_FAMILIES: dict[str, tuple[str, ...]] = {
    "measured:": (MEASURED,),
    "poster-": ("poster-hash:",),
    "logo": (f"logo{LOGO_RULE}:",),
    "textless": (f"textless{TEXTLESS_RULE}:", f"textless-all{TEXTLESS_RULE}:"),
    "titled:": (),
}


class NotFoundError(Exception):
    pass


class UnmatchedError(NotFoundError):
    pass


def art_source(path: str) -> str:
    if apple.is_apple(path):
        return "Apple TV"
    if is_fanart(path):
        return "fanart.tv"
    if path.startswith(overrides.FILE_PREFIX):
        return "custom file"
    return f"TMDB {path}" if path.startswith("/") else path


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


@dataclass
class Context:
    settings: Settings
    sources: Sources
    server: MediaServer | None = None
    measures: Measures = field(init=False)

    def __post_init__(self) -> None:
        self.measures = Measures(self.sources, MEASURED)


def _no_art(ctx: Context, name: str) -> NotFoundError:
    sources = "TMDB and fanart.tv have" if ctx.sources.fanart is not None else "TMDB has"
    return NotFoundError(f"{sources} no textless art for {name}")


def resolve_tmdb(ctx: Context, item: Item, kind: Kind) -> int | None:
    tid = tmdb_id(item)
    if tid is not None:
        return tid
    ids = external_ids(item)
    for source in ("imdb", "tvdb"):
        if source in ids and (found := ctx.sources.find(kind, source, ids[source])) is not None:
            return found
    return None


def _require_tmdb(ctx: Context, item: Item, kind: Kind) -> int:
    tid = resolve_tmdb(ctx, item, kind)
    if tid is not None:
        return tid
    raise UnmatchedError(
        f"{item.get('title')} ({item.get('ratingKey')}) has no TMDB id, and TMDB knows no IMDb or TVDB id of it"
    )


def _season_label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


def _season_art(ctx: Context, title: Title, season: int, show_art: str, numbers: Sequence[int]) -> tuple[str, str]:
    key = ("seasons", str(title.tmdb_id), *map(str, numbers))
    assignment: dict[int, tuple[str, str]] = ctx.sources.remember(
        key, lambda: _assign_seasons(ctx, title, show_art, numbers)
    )
    return assignment.get(season, (show_art, f"show art from {art_source(show_art)}"))


def _season_assignment_paths(
    ctx: Context, title: Title, show_art: str, siblings: Sequence[int], season: int | None
) -> list[str]:
    if season is None:
        return []
    key = ("seasons", str(title.tmdb_id), *map(str, siblings))
    assignment: dict[int, tuple[str, str]] = ctx.sources.remember(
        key, lambda: _assign_seasons(ctx, title, show_art, siblings)
    )
    return [show_art, *(path for number, (path, _) in assignment.items() if number != season)]


def _assign_seasons(ctx: Context, title: Title, show_art: str, numbers: Sequence[int]) -> dict[int, tuple[str, str]]:
    """TMDB stores one picture under several file names and crops, so "used" compares pictures, not names."""
    base = f"{title.kind}:{title.tmdb_id}"
    used = [show_art]
    assignment: dict[int, tuple[str, str]] = {}
    own: dict[int, str] = {}
    for number in numbers:
        refs = ctx.sources.season_images(title.tmdb_id, number).textless_posters() or [
            r
            for r in ctx.sources.fanart_images(title.kind, title.tmdb_id).seasons.get(number, [])
            if r.language is None
        ]
        picked = ctx.sources.picker.textless(f"{base}:s{number}", refs, title.all_titles)
        if picked is None:
            continue
        own[number] = picked.path
        if not _seen(ctx, picked.path, used):
            used.append(picked.path)
            assignment[number] = (picked.path, f"season art from {art_source(picked.path)}")
    pool = ctx.sources.picker.textless_pool(
        f"{base}:pool", ctx.sources.images(title.kind, title.tmdb_id).textless_art(), title.all_titles
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
                f"{'season' if choice == own.get(number) else 'series'} art from {art_source(choice)}",
            )
    return assignment


def _next_unused(ctx: Context, title: Title, avoid: Sequence[str], skip: frozenset[str]) -> str | None:
    base = f"{title.kind}:{title.tmdb_id}"
    pool = ctx.sources.picker.textless_pool(
        f"{base}:pool", ctx.sources.images(title.kind, title.tmdb_id).textless_art(), title.all_titles
    )
    used = [*avoid, *skip]
    return next((path for path in pool if not _seen(ctx, path, used)), None)


def _seen(ctx: Context, path: str, used: Sequence[str], *, redrawn: bool = True) -> bool:
    return any(path == other or ctx.measures.same_picture(path, other, redrawn=redrawn) for other in used)


def _bottom_ink(  # noqa: PLR0913
    ctx: Context, title: Title, art: str, logo: str | None, below: list[lines.Line], *, label: lines.Label | None
) -> tuple[str | None, bool, bool]:
    """The logo to draw, whether it is dark with no fade, and whether a one-colour logo is drawn dark.

    A one-colour logo is drawn dark itself, so every poster of a title keeps one logo design.
    """
    if logo is None:
        return None, ctx.measures.dark_reads(art, None, title.name, below, label=label, prefer=title.font), False
    if ctx.measures.one_colour(logo):
        dark = ctx.measures.dark_reads(art, logo, title.name, below, label=label)
        return logo, dark, dark
    dark_logo = ctx.sources.logo(title, dark=True)
    if dark_logo is not None and ctx.measures.dark_reads(art, dark_logo, title.name, below, label=label):
        return dark_logo, True, False
    return logo, False, False


def _drawn_with(
    text_logo: str | None, dark_bottom: bool, dark_corner: bool, fade: float, prefer: str = ""
) -> dict[str, Any]:
    """Plan inputs that differ from the default look, so unchanged posters keep their fingerprints."""
    drawn: dict[str, Any] = {}
    if text_logo is not None:
        drawn["text_logo"] = text_logo
        if (family := family_for(text_logo, prefer)) != "Inter":
            drawn["font"] = family
    if dark_bottom:
        drawn["ink"] = "dark"
    if dark_corner:
        drawn["corner"] = "dark"
    if fade != 1.0:
        drawn["fade"] = fade
    return drawn


def _lines_below(
    season: int | None, badges: list[quality.Badge], access: list[quality.Badge] | None
) -> list[lines.Line]:
    below: list[lines.Line] = []
    if season == 0:
        below.append(lines.Caption(_season_label(0)))
    if badges:
        below.append(lines.Badges(tuple(badges)))
    if access:
        below.append(lines.Badges(tuple(access)))
    return below


def _poster(  # noqa: PLR0913
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
    override = ctx.sources.overrides(key)
    show_art = None if override and override.custom else ctx.sources.art(title)
    logo = ctx.sources.logo(title)
    extra: dict[str, Any] = {}
    if override and override.custom:
        art_path, note = overrides.FILE_PREFIX + override.custom, "custom art"
        extra["override"] = Path(override.custom).name
    elif show_art is None:
        raise _no_art(ctx, name)
    else:
        if season is None:
            art_path, note = show_art.path, f"art from {art_source(show_art.path)}"
        else:
            art_path, note = _season_art(ctx, title, season, show_art.path, siblings)
        if override and override.skip:
            others = _season_assignment_paths(ctx, title, show_art.path, siblings, season)
            replacement = _next_unused(ctx, title, others, override.skip)
            if replacement is not None:
                art_path, note = replacement, f"next art from {art_source(replacement)}"
            else:
                note += ", no other art left to switch to"
            extra["override"] = sorted(override.skip)
    below = _lines_below(season, badges, access)
    number = season or None
    service = title.service if season is None else None
    logo_path, dark_bottom, recoloured = _bottom_ink(ctx, title, art_path, logo, below, label=label)
    corner = "mark" if service else "number" if number is not None else None
    dark_corner = corner is not None and ctx.measures.corner_dark(art_path, corner)
    ink = NEAR_BLACK if dark_bottom else WHITE
    corner_ink = NEAR_BLACK if dark_corner else WHITE
    fade = 1.0 if dark_bottom else ctx.measures.fade(art_path, logo_path, title.name, below, title.font)

    def draw() -> Image.Image:
        if logo_path is None:
            mark = designs.text_logo(title.name, ink, title.font)
        else:
            mark = trim(ctx.sources.fetch(logo_path))
            mark = designs.recolour(mark, ink) if recoloured else mark
        return designs.tile_poster(
            ctx.sources.load(art_path), mark, lines_below=below, label=label, number=number, service=service,
            ink=ink, corner_ink=corner_ink, fade=fade,
        )  # fmt: skip

    if recoloured:
        extra["logo_ink"] = "dark"
    if service and not dark_corner:
        extra["corner"] = "light"
    extra.update(_drawn_with(None if logo_path else title.name, dark_bottom, dark_corner, fade, title.font))
    inputs = {
        "design": "tile", "art": art_path, "logo": logo_path, "label": label, "lines": below,
        "number": number, "service": service, **extra,
    }  # fmt: skip
    return Plan(key, "poster", name, inputs, draw, [note])


def _background(ctx: Context, title: Title, key: str) -> list[Plan]:
    try:
        picked = ctx.sources.backdrop(title)
    except http.RequestError as exc:
        log.warning("background skipped until the next pass", extra={"title": title.name, "reason": str(exc)})
        return []
    if picked is None:
        return []
    path = picked.path

    def draw() -> Image.Image:
        return designs.background(ctx.sources.fetch(path))

    return [
        Plan(key, "art", title.name, {"design": "background", "art": path}, draw, [f"backdrop from {art_source(path)}"])
    ]


def movie(ctx: Context, item: Item) -> list[Plan]:
    key = str(item["ratingKey"])
    title = ctx.sources.title("movie", _require_tmdb(ctx, item, "movie"), str(item.get("title", "")))
    badges = quality.badges(quality.best(item.get("Media") or []), ctx.settings.minimums)
    access = quality.accessibility(item.get("Media") or [], ctx.settings.accessibility)
    label = _label(ctx, status.Dates(added=status.from_timestamp(item.get("addedAt"))), ctx.settings.leaving(key))
    poster = _poster(ctx, title, key, title.name, season=None, badges=badges, label=label, access=access)
    return [poster, *_background(ctx, title, key)]


def show(ctx: Context, item: Item) -> list[Plan]:
    key = str(item["ratingKey"])
    title = ctx.sources.title("tv", _require_tmdb(ctx, item, "tv"), str(item.get("title", "")))
    dates = status.Dates(
        added=status.from_timestamp(item.get("addedAt")),
        newest_season=_newest(ctx, item, "season", "show.id", key),
        newest_episode=_newest(ctx, item, "episode", "show.id", key),
        next_season=status.next_season(ctx.sources.details("tv", title.tmdb_id)) if ctx.settings.labels else None,
    )
    poster = _poster(
        ctx, title, key, title.name, season=None, badges=[], label=_label(ctx, dates, ctx.settings.leaving(key))
    )
    return [poster, *_background(ctx, title, key)]


def season(ctx: Context, title: Title, item: Item, show_added: date | None = None) -> list[Plan]:
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    leaving = ctx.settings.leaving(key, str(item.get("parentRatingKey", "")))
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
    return status.label(dates if ctx.settings.labels else status.Dates(), ctx.settings.today, leaving)


def _newest(ctx: Context, item: Item, kind: str, field: str, key: str) -> date | None:
    section = str(item.get("librarySectionID", ""))
    if not ctx.settings.labels or ctx.server is None or not section:
        return None
    server = ctx.server
    found = ctx.sources.remember(
        ("newest", kind, field, key), lambda: server.newest_added(section, kind, **{field: key})
    )
    return status.from_timestamp(found)


def _sibling_seasons(ctx: Context, title: Title, item: Item) -> list[int]:
    parent = str(item.get("parentRatingKey", ""))
    if ctx.server is not None and parent:
        children = ctx.sources.remember(("children", parent), lambda: ctx.server.children(parent) if ctx.server else [])
        return sorted(int(child.get("index", 0)) for child in children)
    return sorted(int(s["season_number"]) for s in ctx.sources.details("tv", title.tmdb_id).get("seasons") or [])


def episode(ctx: Context, title: Title, item: Item) -> list[Plan]:
    if ctx.settings.episodes == EpisodeMode.OFF:
        return []
    key, number = str(item["ratingKey"]), int(item.get("index", 0))
    season_number, name = int(item.get("parentIndex", 0)), str(item.get("title", ""))
    still = (ctx.sources.tmdb.episode(title.tmdb_id, season_number, number) or {}).get("still_path")
    if not still:
        return []
    path = str(still)

    titled = ctx.settings.episodes == EpisodeMode.TITLED

    def draw() -> Image.Image:
        return designs.episode_still(ctx.sources.fetch(path), number, name if titled else None, title.font)

    inputs = {
        "design": "episode",
        "still": path,
        **({"number": number, "title": name} if titled else {"mode": "plain"}),
        **({"font": family} if titled and (family := family_for(name, title.font)) != "Inter" else {}),
    }
    return [
        Plan(
            key,
            "thumb",
            f"{title.name} · S{season_number} E{number} · {name}",
            inputs,
            draw,
            [f"still from {art_source(path)}"],
        )
    ]


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
    title = ctx.sources.title(kind, featured_id, str(featured.get("title", "")))
    art = ctx.sources.art(title)
    if art is None:
        raise _no_art(ctx, f"{title.name}, the newest title in {name}")
    art_path = art.path
    service = services.service_named(name)
    if service:
        logo = ctx.sources.logo(title)

        def channel() -> Image.Image:
            featured_logo = trim(ctx.sources.fetch(logo)) if logo else designs.text_logo(title.name, prefer=title.font)
            return designs.channel_tile(ctx.sources.fetch(art_path), featured_logo, service)

        inputs = {"design": "channel", "art": art_path, "logo": logo, "featured": title.name, "service": service}
        if not logo and (family := family_for(title.name, title.font)) != "Inter":
            inputs["font"] = family
        return [Plan(key, "poster", name, inputs, channel, [f"art of {title.name} from {art_source(art_path)}"])]

    def tile() -> Image.Image:
        return category.category_tile(ctx.sources.fetch(art_path), name)

    inputs = {"design": "category", "art": art_path, "title": name, "palette": category.palette_for(name)}
    return [Plan(key, "poster", name, inputs, tile, [f"art of {title.name} from {art_source(art_path)}"])]


def _show(ctx: Context, item: Item) -> tuple[Title, Item]:
    if ctx.server is None:
        raise NotFoundError("No media server is configured")
    show_key = str(item["parentRatingKey"] if item.get("type") == "season" else item["grandparentRatingKey"])
    show_item = ctx.server.item(show_key)
    if show_item is None:
        raise NotFoundError(f"{ctx.server.name} has no show {show_key}")
    return ctx.sources.title("tv", _require_tmdb(ctx, show_item, "tv"), str(show_item.get("title", ""))), show_item


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


def art_candidates(ctx: Context, item: Item, *, next_art: bool = False) -> list[tuple[str, list[ImageRef]]]:
    """The poster art a movie, show or season can get, grouped in the order the picker checks it."""
    kind = item.get("type")
    if kind == "season":
        title = _show(ctx, item)[0]
        number = int(item.get("index", 0))
        own = ctx.sources.season_images(title.tmdb_id, number).textless_posters()
        group = f"TMDB {_season_label(number).lower()} posters"
        if not own:
            own = [
                r for r in ctx.sources.fanart_images("tv", title.tmdb_id).seasons.get(number, []) if r.language is None
            ]
            group = f"fanart.tv {_season_label(number).lower()} posters"
        pool = ctx.sources.images("tv", title.tmdb_id).textless_art()[:POOL_SIZE]
        return [(group, own[:MAX_CANDIDATES]), ("TMDB series art", pool)]
    if kind not in ("movie", "show"):
        raise NotFoundError(f"{item.get('title')} is not a movie, show or season")
    tmdb_kind: Kind = "movie" if kind == "movie" else "tv"
    title = ctx.sources.title(tmdb_kind, _require_tmdb(ctx, item, tmdb_kind), str(item.get("title", "")))
    images = ctx.sources.images(tmdb_kind, title.tmdb_id)
    groups: list[tuple[str, list[ImageRef]]] = [("TMDB posters", images.textless_posters()[:MAX_CANDIDATES])]
    if (url := ctx.sources.apple_art(title)) is not None:
        groups.append(("Apple TV art", [ImageRef(url, None, 1680, 3636, 0.0, 0)]))
    groups.append(("TMDB backdrops", images.textless_backdrops()[:MAX_CANDIDATES]))
    if ctx.sources.fanart is not None:
        extra = ctx.sources.fanart_images(tmdb_kind, title.tmdb_id).images
        groups += [
            ("fanart.tv posters", extra.textless_posters()[:MAX_CANDIDATES]),
            ("fanart.tv backdrops", extra.textless_backdrops()[:MAX_CANDIDATES]),
        ]
    if next_art:
        groups.append(("TMDB art for art next", images.textless_art()[:POOL_SIZE]))
    return groups


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
    details = ctx.sources.tmdb.details(kind, tid)
    name = str(details.get("title") or details.get("name") or tid)
    item: Item = {"ratingKey": f"tmdb-{kind}-{tid}", "title": name, "Guid": [{"id": f"tmdb://{tid}"}]}
    if kind == "movie":
        return movie(ctx, item)
    plans = show(ctx, item)
    title = ctx.sources.title("tv", tid, name)
    for number in seasons:
        plans += season(ctx, title, {"ratingKey": f"tmdb-tv-{tid}-s{number}", "index": number})
    return plans
