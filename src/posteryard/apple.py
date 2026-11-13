import json
import logging
import re
from typing import Any

from posteryard import http
from posteryard.tmdb import Details, ImageRef, Kind

log = logging.getLogger(__name__)
WIKIDATA = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
PAGE = "https://tv.apple.com/{region}/{kind}/title/{umc}"
# Wikidata properties: Apple TV movie ID and Apple TV show ID.
PROPERTIES: dict[Kind, str] = {"movie": "P9586", "tv": "P9751"}
UMC = re.compile(r"^umc\.cmc\.[a-z0-9]+$")
QID = re.compile(r"^Q\d+$")
IMAGE_HOST = re.compile(r"^https://is\d-ssl\.mzstatic\.com/image/thumb/")
TALL_SIZE = (1680, 3636)
TALL = f"{TALL_SIZE[0]}x{TALL_SIZE[1]}"


def tall_ref(url: str) -> ImageRef:
    return ImageRef(url, None, *TALL_SIZE, 0.0, 0)


def is_apple(path: str) -> bool:
    return bool(IMAGE_HOST.match(path))


def apple_id(kind: Kind, details: Details) -> str | None:
    qid = str((details.get("external_ids") or {}).get("wikidata_id") or "")
    if not QID.match(qid):
        return None
    try:
        entity = http.get_json(WIKIDATA.format(qid=qid), check_redirects=True)
    except http.HttpError as exc:
        if exc.status == 404:
            return None
        raise
    entities = _object(_object(entity).get("entities"))
    claims = _object(_object(next(iter(entities.values()), None)).get("claims"))
    found = claims.get(PROPERTIES[kind]) or []
    if not isinstance(found, list):
        raise ValueError("the Wikidata answer is not readable")
    for claim in found:
        value = str(_object(_object(_object(claim).get("mainsnak")).get("datavalue")).get("value") or "")
        if UMC.match(value):
            return value
    return None


def _object(value: object) -> dict[str, Any]:
    """Missing is empty. Any other shape raises: a page Posteryard cannot read says nothing about the title."""
    if value is None or value == []:
        return {}
    if not isinstance(value, dict):
        raise ValueError("the Wikidata answer is not readable")
    return value


def tall_art(kind: Kind, umc: str, region: str) -> str | None:
    page = http.request(
        "GET",
        PAGE.format(region=region.lower(), kind="show" if kind == "tv" else "movie", umc=umc),
        check_redirects=True,
    ).decode("utf-8", "replace")
    match = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if match is None:
        raise ValueError("the Apple TV page has no data block")
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
    """Apple TV's tall art for a TMDB title. A page Posteryard cannot read raises: it says nothing about the title."""
    umc = apple_id(kind, details)
    if umc is None:
        return None
    try:
        return tall_art(kind, umc, region)
    except http.HttpError as exc:
        if exc.status == 404:
            return None
        raise
    except (ValueError, KeyError, RecursionError) as exc:
        raise ValueError(f"the Apple TV page is not readable: {type(exc).__name__}") from None
