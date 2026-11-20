"""Record real TMDB, fanart.tv, Wikidata and Apple TV answers for tests/recorded_answers.json, or check them live.

Usage: TMDB_API_KEY=... [FANART_API_KEY=...] uv run python tools/record_answers.py [--check]

--check reads the live services and checks them like tests/test_recorded_answers.py, without writing.
"""

import json
import os
import sys
import urllib.parse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from posteryard import apple, http
from posteryard.fanart import Fanart
from posteryard.tmdb import Tmdb

OUT = Path(__file__).resolve().parent.parent / "tests" / "recorded_answers.json"
LIST_LIMIT = 30
REGIONS = ("US", "GB", "DE")
DROPPED = frozenset({"crew", "guest_stars"})
MOVIE, SHOW, NETWORK, IMDB = 603, 1396, 174, "tt0133093"


def key_of(url: str) -> str:
    """Credentials never reach the file: the key is the redacted address."""
    parts = urllib.parse.urlsplit(http.redact(url))
    query = sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query), fragment=""))


def _shorten(node: Any, parent: str = "") -> Any:
    if isinstance(node, list):
        limit = 2 if parent == "episodes" else 5 if parent == "results" else LIST_LIMIT
        return [_shorten(value, parent) for value in node[:limit]]
    if isinstance(node, dict):
        if parent == "watch/providers":
            node = {**node, "results": {r: v for r, v in (node.get("results") or {}).items() if r in REGIONS}}
        return {key: _shorten(value, key) for key, value in node.items() if key not in DROPPED}
    return node


def _apple_path(node: Any, parent: str = "") -> Any:
    """Keep only the branches that lead to a tall template, as apple.tall_art walks them."""
    if isinstance(node, dict):
        if parent == "tall" and isinstance(node.get("template"), str):
            return {"template": node["template"]}
        kept = {k: v for k, v in ((k, _apple_path(v, k)) for k, v in node.items()) if v is not None}
        return kept or None
    if isinstance(node, list):
        kept_list = [v for v in (_apple_path(v, parent) for v in node) if v is not None]
        return kept_list or None
    return None


def _trim(url: str, body: bytes) -> Any:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "tv.apple.com":
        text = body.decode("utf-8", "replace")
        start = text.index('id="serialized-server-data"')
        data = json.loads(text[text.index(">", start) + 1 : text.index("</script>", start)])
        return {"apple_page": _apple_path(data)}
    data = json.loads(body)
    if host == "www.wikidata.org":
        entities = data.get("entities") or {}
        return {
            "entities": {
                qid: {"claims": {p: c for p, c in (e.get("claims") or {}).items() if p in apple.PROPERTIES.values()}}
                for qid, e in entities.items()
            }
        }
    return _shorten(data)


def body_of(answer: Any) -> bytes:
    if isinstance(answer, dict) and "apple_page" in answer:
        data = json.dumps(answer["apple_page"])
        return f'<html><script type="application/json" id="serialized-server-data">{data}</script></html>'.encode()
    return json.dumps(answer).encode()


def exercise(tmdb_key: str, fanart_key: str) -> dict[str, Any]:
    """Every call whose answer shape Posteryard depends on. The summary is what the tests check."""
    tmdb = Tmdb(tmdb_key)
    movie = tmdb.details("movie", MOVIE)
    images = tmdb.images("movie", MOVIE)
    season = tmdb.season_images(SHOW, 1)
    episode = tmdb.episode(SHOW, 1, 1)
    summary: dict[str, Any] = {
        "title": movie.get("title"),
        "titles": tmdb.all_titles("movie", MOVIE),
        "posters": len(images.posters),
        "textless": len(images.textless_art()),
        "logos": len(images.logos_in(["en"])),
        "language_logos": len(tmdb.language_logos("movie", MOVIE, ["en"])),
        "regions": sorted(tmdb.watch_providers("movie", MOVIE)),
        "show": tmdb.details("tv", SHOW).get("name"),
        "tvdb": tmdb.tvdb_id("tv", SHOW),
        "season_posters": len(season.posters),
        "episode": episode.get("name") if episode else None,
        "found": tmdb.find("imdb", IMDB),
        "search": [r.get("id") for r in tmdb.search("movie", "The Matrix")],
        "network_logo": tmdb.network_logo(NETWORK),
        "apple": apple.find("movie", movie, "US"),
    }
    if fanart_key:
        found = Fanart(fanart_key).images("movie", MOVIE, None)
        summary["fanart_posters"] = len(found.images.posters)
    return summary


def problems(summary: dict[str, Any]) -> list[str]:
    checks: dict[str, Callable[[Any], bool]] = {
        "title": lambda v: v == "The Matrix",
        "titles": lambda v: "The Matrix" in v and len(v) > 1,
        "posters": lambda v: v > 0,
        "textless": lambda v: v > 0,
        "logos": lambda v: v > 0,
        "language_logos": lambda v: v > 0,
        "regions": lambda v: "US" in v,
        "show": lambda v: v == "Breaking Bad",
        "tvdb": lambda v: v == 81189,
        "season_posters": lambda v: v > 0,
        "episode": bool,
        "found": lambda v: v == {"movie": MOVIE},
        "search": lambda v: MOVIE in v,
        "network_logo": lambda v: bool(v) and str(v).endswith((".png", ".svg")),
        "apple": lambda v: v is not None and apple.is_apple(v),
        "fanart_posters": lambda v: v > 0,
    }
    return [
        f"{name}: {summary.get(name)!r}" for name, ok in checks.items() if name in summary and not ok(summary[name])
    ]


@contextmanager
def recording(answers: dict[str, Any]) -> Iterator[None]:
    real = http.request

    def record(method: str, url: str, **kwargs: Any) -> bytes:
        body = real(method, url, **kwargs)
        answers[key_of(url)] = _trim(url, body)
        return body_of(answers[key_of(url)])

    http.request = record
    try:
        yield
    finally:
        http.request = real


def main(args: list[str]) -> int:
    tmdb_key = os.environ.get("TMDB_API_KEY", "").strip()
    fanart_key = os.environ.get("FANART_API_KEY", "").strip()
    if not tmdb_key or args not in ([], ["--check"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    answers: dict[str, Any] = {}
    with recording(answers):
        summary = exercise(tmdb_key, fanart_key)
    if found := problems(summary):
        print("Answers changed shape:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    if args == ["--check"]:
        print(f"The {len(answers)} live answers still read as expected.")
        return 0
    OUT.write_text(json.dumps(answers, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print(f"{len(answers)} answers written to {OUT.relative_to(OUT.parent.parent)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
