"""Streaming service collections in TV libraries: one per service, kept in step with the service marks.

Only collections that carry MANAGED_LABEL are changed or deleted. A collection of the same name made by hand or by
another tool is left alone.
"""

import logging

from posteryard import http, services
from posteryard.pipeline import Context
from posteryard.server import MediaServer, tmdb_id

log = logging.getLogger(__name__)
MANAGED_LABEL = "posteryard-collection"
MIN_SHOWS = 3


def sync(server: MediaServer, ctx: Context, section_key: str) -> list[str]:
    """Create, fill, empty and delete Posteryard's service collections in one TV library. Returns the kept keys.

    When a show's service cannot be looked up, nothing is removed or deleted in that pass.
    """
    groups: dict[str, list[str]] = {}
    unknown: set[str] = set()
    for show in server.section_items(section_key, "show"):
        tid = tmdb_id(show)
        if tid is None:
            continue
        try:
            service = ctx.title("tv", tid, str(show.get("title", ""))).service
        except (http.RequestError, ValueError) as exc:
            log.warning("no streaming service for %s, keeping its collections as they are: %s", show.get("title"), exc)
            unknown.add(str(show["ratingKey"]))
            continue
        if service in services.NAMES:
            groups.setdefault(service, []).append(str(show["ratingKey"]))
    ours = {str(c.get("title")): c for c in server.section_items(section_key, "collection", label=MANAGED_LABEL)}
    kept: list[str] = []
    for service, members in sorted(groups.items()):
        name = services.NAMES[service]
        existing = ours.pop(name, None)
        if len(members) < MIN_SHOWS:
            if existing is not None and unknown:
                kept.append(str(existing["ratingKey"]))
            elif existing is not None:
                server.delete_collection(str(existing["ratingKey"]))
                log.info("removed the %s collection: fewer than %d shows", name, MIN_SHOWS)
            continue
        if existing is None:
            key = server.create_collection(section_key, "show", name, members)
            try:
                server.set_label(section_key, "collection", key, MANAGED_LABEL)
            except http.RequestError:
                server.delete_collection(key)
                raise
            log.info("created the %s collection with %d shows", name, len(members))
        else:
            key = str(existing["ratingKey"])
            current = {str(m["ratingKey"]) for m in server.collection_children(key)}
            missing = [m for m in members if m not in current]
            if missing:
                server.add_to_collection(key, missing)
            for gone in [] if unknown else sorted(current - set(members)):
                server.remove_from_collection(key, gone)
        kept.append(key)
    if unknown:
        return kept + [str(c["ratingKey"]) for c in ours.values()]
    for leftover in ours.values():
        server.delete_collection(str(leftover["ratingKey"]))
        log.info("removed the %s collection: no show uses the service any more", leftover.get("title"))
    return kept
