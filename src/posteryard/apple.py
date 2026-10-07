import json
import logging
import re
from typing import Any

from posteryard import http
from posteryard.tmdb import Details, Kind

log = logging.getLogger(__name__)
WIKIDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
PAGE = "https://tv.apple.com/{region}/{kind}/title/{umc}"
# Wikidata properties: Apple TV movie ID and Apple TV show ID.
PROPERTIES: dict[Kind, str] = {"movie": "P9586", "tv": "P9751"}
UMC = re.compile(r"^umc\.cmc\.[a-z0-9]+$")
QID = re.compile(r"^Q\d+$")
IMAGE_HOST = re.compile(r"^https://is\d-ssl\.mzstatic\.com/image/thumb/")
TALL = "1680x3636"
HEADERS = {"User-Agent": "Posteryard (https://github.com/SandObserver/Posteryard)"}


def is_apple(path: str) -> bool:
    return bool(IMAGE_HOST.match(path))


def apple_id(kind: Kind, details: Details) -> str | None:
    qid = str((details.get("external_ids") or {}).get("wikidata_id") or "")
    if not QID.match(qid):
        return None
    try:
        entity = http.get_json(WIKIDATA.format(qid=qid), HEADERS)
    except http.HttpError as exc:
        if exc.status == 404:
            return None
        raise
    entities: dict[str, Any] = entity.get("entities") or {}
    first: dict[str, Any] = next(iter(entities.values()), {})
    claims: dict[str, Any] = first.get("claims") or {}
    for claim in claims.get(PROPERTIES[kind]) or []:
        value = str(((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or "")
        if UMC.match(value):
            return value
    return None


def tall_art(kind: Kind, umc: str, region: str) -> str | None:
    page = http.request(
        "GET", PAGE.format(region=region.lower(), kind="show" if kind == "tv" else "movie", umc=umc), headers=HEADERS
    ).decode("utf-8", "replace")
    match = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if match is None:
        return None
    templates: list[str] = []

    def walk(node: object, parent: str) -> None:
        if isinstance(node, dict):
            if parent == "tall" and isinstance(node.get("template"), str):
                templates.append(node["template"])
            for key, value in node.items():
                walk(value, key)
        elif isinstance(node, list):
            for value in node:
                walk(value, parent)

    walk(json.loads(match.group(1)), "")
    url = next((t.replace("{w}x{h}", TALL).replace("{f}", "jpg") for t in templates if "{w}x{h}" in t), None)
    return url if url and is_apple(url) else None


def find(kind: Kind, details: Details, region: str) -> str | None:
    """Apple TV's tall art for a TMDB title. A page Posteryard cannot read counts as none; a network error raises."""
    umc = apple_id(kind, details)
    if umc is None:
        return None
    try:
        return tall_art(kind, umc, region)
    except http.HttpError as exc:
        if exc.status == 404:
            return None
        raise
    except (ValueError, KeyError) as exc:
        log.warning("Apple TV page not readable", extra={"umc": umc, "reason": type(exc).__name__})
        return None
