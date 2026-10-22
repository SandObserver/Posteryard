"""Only collections that carry MANAGED_LABEL are changed or deleted."""

import logging
import time
from collections import Counter
from http import HTTPStatus

from posteryard import http, pipeline, services
from posteryard.pipeline import Context
from posteryard.server import MediaServer

log = logging.getLogger(__name__)
MANAGED_LABEL = "posteryard-collection"
MIN_SHOWS = 3
CLOCK_SLACK = 600


def _groups(
    server: MediaServer, ctx: Context, section_keys: list[str]
) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """Shows by service name, shows whose service lookup failed, and shows the server or TMDB has not matched."""
    spellings: dict[str, Counter[str]] = {}
    members: dict[str, list[str]] = {}
    unknown: set[str] = set()
    unmatched: set[str] = set()
    for show in (show for key in section_keys for show in server.section_items(key, "show")):
        try:
            tid = pipeline.resolve_tmdb(ctx, show, "tv")
            if tid is None:
                unmatched.add(str(show["ratingKey"]))
                continue
            name = ctx.sources.title("tv", tid, str(show.get("title", ""))).service_name
        except (http.RequestError, OSError, ValueError) as exc:
            if isinstance(exc, http.HttpError) and exc.status == HTTPStatus.NOT_FOUND:
                log.warning("show not on TMDB, collections kept as they are", extra={"title": show.get("title")})
                unmatched.add(str(show["ratingKey"]))
                continue
            log.warning(
                "streaming service unknown, collections kept as they are",
                extra={"title": show.get("title"), "reason": str(exc)},
            )
            unknown.add(str(show["ratingKey"]))
            continue
        if name:
            group = services.network_key(name)
            spellings.setdefault(group, Counter())[name] += 1
            members.setdefault(group, []).append(str(show["ratingKey"]))
    named = {min(spellings[g].items(), key=lambda item: (-item[1], item[0]))[0]: keys for g, keys in members.items()}
    return named, unknown, unmatched


def _children(server: MediaServer, key: str) -> set[str]:
    return {str(m["ratingKey"]) for m in server.collection_children(key)}


def _pending(section_key: str, name: str) -> str:
    return f"collection-create:{section_key}:{name}"


def _create(server: MediaServer, ctx: Context, section_key: str, name: str, members: list[str]) -> str:
    """Remember the attempt first. A create whose answer is lost still makes the collection on the server."""
    pending = _pending(section_key, name)
    ctx.sources.choices.put_choice(pending, {"at": int(time.time())})
    key = server.create_collection(section_key, "show", name, members)
    try:
        server.set_label(section_key, "collection", key, MANAGED_LABEL)
    except http.RequestError:
        server.delete_collection(key)
        raise
    ctx.sources.choices.put_choice(pending, {})
    log.info("collection created", extra={"collection": name, "shows": len(members)})
    return key


def _adopt(server: MediaServer, ctx: Context, section_key: str, name: str, labelled: set[str]) -> str | None:
    """A collection left by a create whose answer was lost: same name, no label, made at the time of the attempt."""
    pending = _pending(section_key, name)
    hit = ctx.sources.choices.get_choice(pending)
    if not hit:
        return None
    found = sorted(
        (c for c in server.section_items(section_key, "collection")
         if c.get("title") == name and str(c["ratingKey"]) not in labelled
         and abs(int(c.get("addedAt") or 0) - int(hit["at"])) <= CLOCK_SLACK),
        key=lambda c: int(c.get("addedAt") or 0),
    )  # fmt: skip
    if not found:
        ctx.sources.choices.put_choice(pending, {})
        return None
    key = str(found[-1]["ratingKey"])
    server.set_label(section_key, "collection", key, MANAGED_LABEL)
    for extra in found[:-1]:
        server.delete_collection(str(extra["ratingKey"]))
    ctx.sources.choices.put_choice(pending, {})
    log.info("collection taken over after a lost answer", extra={"collection": name})
    return key


def sync(server: MediaServer, ctx: Context, section_keys: list[str]) -> list[str]:  # noqa: C901
    """Create, fill, empty and delete Posteryard's service collections for TV libraries. Returns the kept keys.

    Several libraries share one set of collections: only for a server whose collections belong to no library.

    When a show's service cannot be looked up, nothing is removed or deleted in that pass.
    A show the server has not matched keeps the collections it is in.
    """
    groups, unknown, unmatched = _groups(server, ctx, section_keys)
    section_key = section_keys[0]
    ours = {str(c.get("title")): c for c in server.section_items(section_key, "collection", label=MANAGED_LABEL)}
    labelled = {str(c["ratingKey"]) for c in ours.values()}
    kept: list[str] = []
    for name, matched in sorted(groups.items()):
        existing = ours.pop(name, None)
        if existing is None and (adopted := _adopt(server, ctx, section_key, name, labelled)):
            existing = {"ratingKey": adopted, "title": name}
        current = _children(server, str(existing["ratingKey"])) if existing else set()
        members = matched + sorted(current & unmatched)
        if len(members) < MIN_SHOWS:
            if existing is not None and unknown:
                kept.append(str(existing["ratingKey"]))
            elif existing is not None:
                server.delete_collection(str(existing["ratingKey"]))
                log.info("collection removed", extra={"collection": name, "reason": f"fewer than {MIN_SHOWS} shows"})
            continue
        if existing is None:
            key = _create(server, ctx, section_key, name, members)
        else:
            key = str(existing["ratingKey"])
            missing = [m for m in members if m not in current]
            if missing:
                server.add_to_collection(key, missing)
            for gone in [] if unknown else sorted(current - set(members)):
                server.remove_from_collection(key, gone)
        kept.append(key)
    if unknown:
        return kept + [str(c["ratingKey"]) for c in ours.values()]
    for leftover in ours.values():
        if len(_children(server, str(leftover["ratingKey"])) & unmatched) >= MIN_SHOWS:
            kept.append(str(leftover["ratingKey"]))
            continue
        server.delete_collection(str(leftover["ratingKey"]))
        log.info(
            "collection removed", extra={"collection": leftover.get("title"), "reason": "no show uses the service"}
        )
    return kept
