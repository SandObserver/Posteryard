"""Streaming service collections in TV libraries: one per service, kept in step with the service marks.

Only collections that carry MANAGED_LABEL are changed or deleted. A collection of the same name made by hand or by
another tool is left alone.
"""

import logging

from posteryard import services
from posteryard.pipeline import Context
from posteryard.plex import Plex, tmdb_id

log = logging.getLogger(__name__)
MANAGED_LABEL = "posteryard-collection"
MIN_SHOWS = 3


def sync(plex: Plex, ctx: Context, section_key: str) -> list[str]:
    """Create, fill, empty and delete Posteryard's service collections in one TV library. Returns the kept keys."""
    groups: dict[str, list[str]] = {}
    for show in plex.section_items(section_key, "show"):
        tid = tmdb_id(show)
        if tid is None:
            continue
        service = ctx.title("tv", tid, str(show.get("title", ""))).service
        if service in services.NAMES:
            groups.setdefault(service, []).append(str(show["ratingKey"]))
    ours = {str(c.get("title")): c for c in plex.section_items(section_key, "collection", label=MANAGED_LABEL)}
    kept: list[str] = []
    for service, members in sorted(groups.items()):
        name = services.NAMES[service]
        existing = ours.pop(name, None)
        if len(members) < MIN_SHOWS:
            if existing is not None:
                plex.delete_collection(str(existing["ratingKey"]))
                log.info("removed the %s collection: fewer than %d shows", name, MIN_SHOWS)
            continue
        if existing is None:
            key = plex.create_collection(section_key, "show", name, members)
            plex.set_label(section_key, "collection", key, MANAGED_LABEL)
            log.info("created the %s collection with %d shows", name, len(members))
        else:
            key = str(existing["ratingKey"])
            current = {str(m["ratingKey"]) for m in plex.collection_children(key)}
            missing = [m for m in members if m not in current]
            if missing:
                plex.add_to_collection(key, missing)
            for gone in sorted(current - set(members)):
                plex.remove_from_collection(key, gone)
        kept.append(key)
    for leftover in ours.values():
        plex.delete_collection(str(leftover["ratingKey"]))
        log.info("removed the %s collection: no show uses the service any more", leftover.get("title"))
    return kept
