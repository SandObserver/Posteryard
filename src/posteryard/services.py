import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from posteryard.tmdb import RegionOffers

# First match wins. Keep longer names before their prefixes.
SERVICE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("amazon prime video", "prime"),
    ("prime video", "prime"),
    ("apple tv plus", "appletv"),
    ("apple tv+", "appletv"),
    ("apple tv", "appletv"),
    ("disney plus", "disney"),
    ("disney+", "disney"),
    ("paramount plus", "paramountplus"),
    ("paramount+", "paramountplus"),
    ("hbo max", "hbomax"),
    ("max", "hbomax"),
    ("netflix", "netflix"),
    ("crave", "crave"),
    ("hulu", "hulu"),
    ("peacock", "peacock"),
    ("youtube", "youtube"),
    ("crunchyroll", "crunchyroll"),
    ("tubi tv", "tubi"),
    ("tubi", "tubi"),
    ("pluto tv", "plutotv"),
    ("starz", "starz"),
    ("mubi", "mubi"),
    ("viaplay", "viaplay"),
    ("sky go", "sky"),
    ("sky x", "sky"),
    ("now tv", "now"),
    ("now", "now"),
    ("rtl+", "rtl"),
    ("movistar plus+", "movistar"),
    ("channel 4", "channel4"),
    ("animation digital network", "adn"),
    ("anime digital network", "adn"),
)
NAMES = {
    "appletv": "Apple TV",
    "crave": "Crave",
    "disney": "Disney+",
    "hbomax": "HBO Max",
    "hulu": "Hulu",
    "netflix": "Netflix",
    "paramountplus": "Paramount+",
    "peacock": "Peacock",
    "prime": "Prime Video",
    "youtube": "YouTube",
    "crunchyroll": "Crunchyroll",
    "tubi": "Tubi",
    "plutotv": "Pluto TV",
    "starz": "Starz",
    "mubi": "MUBI",
    "viaplay": "Viaplay",
    "sky": "Sky",
    "now": "NOW",
    "rtl": "RTL+",
    "movistar": "Movistar Plus+",
    "channel4": "Channel 4",
    "adn": "ADN",
}
EXCLUDED_WORDS = (
    "amazon channel", "apple tv channel", "roku premium channel", "plex channel", "store", "youtube tv", "fubo",
    "stacktv", "live tv", "justwatch", "spectrum on demand", "philo", "sling tv",
)  # fmt: skip
OFFER_TYPES: tuple[Literal["flatrate", "free", "ads"], ...] = ("flatrate", "free", "ads")
AD_SUFFIXES = (" standard with ads", " basic with ads", " with ads")
NOT_KEY = re.compile(r"[^a-z0-9+]")
COLLECTION_SUFFIXES = ("", " movies", " shows", " tv shows", " series", " originals", " kids")


def service_for(provider_name: str) -> str | None:
    name = provider_name.strip().lower()
    if any(word in name for word in EXCLUDED_WORDS):
        return None
    for suffix in AD_SUFFIXES:
        name = name.removesuffix(suffix)
    for pattern, service in SERVICE_PATTERNS:
        if name == pattern or name.startswith(pattern + " "):
            return service
    return None


def collection_bases(name: str) -> set[str]:
    """The service names a collection name can stand for, such as `netflix` for `Netflix Movies`."""
    name = name.strip().lower()
    return {name.removesuffix(suffix) for suffix in COLLECTION_SUFFIXES if name.endswith(suffix)}


def service_named(name: str) -> str | None:
    if key := next((key for key, label in NAMES.items() if label == name), None):
        return key
    bases = collection_bases(name)
    return next((service for pattern, service in SERVICE_PATTERNS if pattern in bases), None)


def display_name(provider_name: str) -> str:
    name = provider_name.strip()
    for suffix in AD_SUFFIXES:
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name


def network_key(name: str) -> str:
    name = name.strip().lower()
    for suffix in AD_SUFFIXES:
        name = name.removesuffix(suffix)
    return NOT_KEY.sub("", name.replace("plus", "+"))


@dataclass(frozen=True)
class Offer:
    provider_id: int
    name: str
    logo_path: str
    region: str = ""


def _priority(provider: Mapping[str, object]) -> int:
    value = provider.get("display_priority")
    return value if isinstance(value, int) else 999


def offers(providers_by_region: Mapping[str, RegionOffers], regions: Iterable[str]) -> Iterator[Offer]:
    for region in regions:
        by_type = providers_by_region.get(region) or {}
        for offer_type in OFFER_TYPES:
            for provider in sorted(by_type.get(offer_type) or [], key=_priority):
                name = str(provider.get("provider_name", ""))
                if any(word in name.lower() for word in EXCLUDED_WORDS):
                    continue
                yield Offer(int(provider.get("provider_id") or 0), name, str(provider.get("logo_path") or ""), region)
