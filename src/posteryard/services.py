from collections.abc import Iterable, Mapping
from typing import Any

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
}
EXCLUDED_WORDS = ("channel", "store", "youtube tv", "fubo", "stacktv", "live tv")
OFFER_TYPES = ("flatrate", "free", "ads")
AD_SUFFIXES = (" standard with ads", " basic with ads", " with ads")


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


def pick(providers_by_region: Mapping[str, Any], regions: Iterable[str]) -> str | None:
    for region in regions:
        offers = providers_by_region.get(region) or {}
        for offer_type in OFFER_TYPES:
            for provider in sorted(offers.get(offer_type) or [], key=lambda p: p.get("display_priority", 999)):
                service = service_for(str(provider.get("provider_name", "")))
                if service:
                    return service
    return None
