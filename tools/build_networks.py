"""Match TMDB watch providers to TMDB networks with a logo, into src/posteryard/assets/networks.json.

Also writes provider_networks.json: for a service with several country networks and none worldwide,
the network its provider id matched in a country that has one, for the countries that have none.

Run when TMDB adds services: TMDB_API_KEY=... uv run python tools/build_networks.py
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from posteryard import http
from posteryard.services import network_key
from posteryard.tmdb import Tmdb

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "posteryard" / "assets" / "networks.json"
PROVIDERS_OUT = ROOT / "src" / "posteryard" / "assets" / "provider_networks.json"
PREFERRED_REGIONS = ("US", "GB")
BLOCK = 500
EMPTY_BLOCKS = 3
WORKERS = 16
DROPPED_WORDS = ("tv", "vip", "premium", "plus", "play", "now", "go")


def network(tmdb: Tmdb, network_id: int) -> dict[str, Any] | None:
    try:
        raw = tmdb._get(f"/network/{network_id}")
    except http.HttpError as exc:
        if exc.status == 404:
            return None
        raise
    return raw if isinstance(raw, dict) and raw.get("logo_path") else None


def all_networks(tmdb: Tmdb) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    start, empty = 1, 0
    with ThreadPoolExecutor(WORKERS) as pool:
        while empty < EMPTY_BLOCKS:
            block = [n for n in pool.map(lambda i: network(tmdb, i), range(start, start + BLOCK)) if n]
            found += block
            empty = 0 if block else empty + 1
            print(f"networks {start} to {start + BLOCK - 1}: {len(block)}", file=sys.stderr)
            start += BLOCK
    return found


def providers(tmdb: Tmdb) -> set[tuple[int, str, str]]:
    """Every provider as (provider id, name, region)."""
    regions = [r["iso_3166_1"] for r in tmdb._get("/watch/providers/regions")["results"]]
    found: set[tuple[int, str, str]] = set()
    for region in regions:
        for kind in ("tv", "movie"):
            for p in tmdb._get(f"/watch/providers/{kind}", watch_region=region)["results"]:
                found.add((int(p["provider_id"]), str(p["provider_name"]), region))
    return found


def provider_networks(table: dict[str, list[list[Any]]], offers: set[tuple[int, str, str]]) -> dict[str, int]:
    """A provider id is one service in every region, so its network from one region serves the others."""
    order = {region: i for i, region in enumerate(PREFERRED_REGIONS)}
    found: dict[str, int] = {}
    for provider_id, name, region in sorted(offers, key=lambda o: (order.get(o[2], len(order)), o[2], o[0])):
        rows = table.get(network_key(name), [])
        if len(rows) < 2 or any(country == "" for _, country in rows) or str(provider_id) in found:
            continue
        if match := next((int(i) for i, country in rows if country == region), None):
            found[str(provider_id)] = match
    return found


def candidates(name: str) -> list[str]:
    words = name.split()
    keys = [network_key(name)]
    if len(words) > 1 and words[-1].lower().strip("+") in DROPPED_WORDS:
        keys.append(network_key(" ".join(words[:-1])))
    return keys


def main() -> None:
    tmdb = Tmdb(os.environ["TMDB_API_KEY"])
    by_key: dict[str, list[list[Any]]] = {}
    for n in sorted(all_networks(tmdb), key=lambda n: int(n["id"])):
        by_key.setdefault(network_key(str(n["name"])), []).append([int(n["id"]), str(n.get("origin_country") or "")])
    table: dict[str, list[list[Any]]] = {}
    offers = providers(tmdb)
    for name in sorted({name for _, name, _ in offers}):
        for key in candidates(name):
            if key in by_key:
                table[network_key(name)] = by_key[key]
                break
    OUT.write_text(json.dumps(table, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    by_provider = provider_networks(table, offers)
    PROVIDERS_OUT.write_text(json.dumps(by_provider, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(table)} providers matched to networks, {len(by_provider)} by provider id", file=sys.stderr)


if __name__ == "__main__":
    main()
