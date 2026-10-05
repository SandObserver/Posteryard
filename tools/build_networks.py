"""Match TMDB watch providers to TMDB networks with a logo, into src/posteryard/assets/networks.json.

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


def provider_names(tmdb: Tmdb) -> set[str]:
    regions = [r["iso_3166_1"] for r in tmdb._get("/watch/providers/regions")["results"]]
    names: set[str] = set()
    for region in regions:
        for kind in ("tv", "movie"):
            names |= {p["provider_name"] for p in tmdb._get(f"/watch/providers/{kind}", watch_region=region)["results"]}
    return names


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
    for name in sorted(provider_names(tmdb)):
        for key in candidates(name):
            if key in by_key:
                table[network_key(name)] = by_key[key]
                break
    OUT.write_text(json.dumps(table, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(table)} providers matched to networks", file=sys.stderr)


if __name__ == "__main__":
    main()
