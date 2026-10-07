"""Only collections that carry MANAGED_LABEL are changed or deleted."""

import logging

from posteryard import http, pipeline, services
from posteryard.pipeline import Context
from posteryard.server import MediaServer

log = logging.getLogger(__name__)
MANAGED_LABEL = "posteryard-collection"
MIN_SHOWS = 3


def sync(server: MediaServer, ctx: Context, section_key: str) -> list[str]:  # noqa: C901, PLR0912
    """Create, fill, empty and delete Posteryard's service collections in one TV library. Returns the kept keys.

    When a show's service cannot be looked up, nothing is removed or deleted in that pass.
    """
    groups: dict[str, list[str]] = {}
    unknown: set[str] = set()
    for show in server.section_items(section_key, "show"):
        try:
            tid = pipeline.resolve_tmdb(ctx, show, "tv")
            if tid is None:
                continue
            service = ctx.title("tv", tid, str(show.get("title", ""))).service
        except (http.RequestError, ValueError) as exc:
            log.warning(
                "streaming service unknown, collections kept as they are",
                extra={"title": show.get("title"), "reason": str(exc)},
            )
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
                log.info("collection removed", extra={"collection": name, "reason": f"fewer than {MIN_SHOWS} shows"})
            continue
        if existing is None:
            key = server.create_collection(section_key, "show", name, members)
            try:
                server.set_label(section_key, "collection", key, MANAGED_LABEL)
            except http.RequestError:
                server.delete_collection(key)
                raise
            log.info("collection created", extra={"collection": name, "shows": len(members)})
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
        log.info(
            "collection removed", extra={"collection": leftover.get("title"), "reason": "no show uses the service"}
        )
    return kept
