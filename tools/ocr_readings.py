"""Record what OCR reads on real TMDB art, for tests/ocr_readings.json.

Usage: TMDB_API_KEY=... uv run python tools/ocr_readings.py movie:603:/path.jpg=clean tv:1396:/path.jpg=text
"""

import json
import os
import re
import sys
from pathlib import Path

from posteryard import ocr
from posteryard.tmdb import Tmdb

OUT = Path(__file__).resolve().parent.parent / "tests" / "ocr_readings.json"
REF = re.compile(r"^(movie|tv):(\d+):(/\S+)=(clean|text)$")


def save(readings: list[dict[str, object]]) -> None:
    OUT.write_text(
        json.dumps(sorted(readings, key=lambda r: (r["name"], r["image"])), ensure_ascii=False, indent=1) + "\n"
    )


def main(args: list[str]) -> int:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    refs = [REF.match(arg) for arg in args]
    bad = [arg for arg, ref in zip(args, refs, strict=True) if ref is None]
    if not key or not args or bad:
        print(__doc__.strip(), file=sys.stderr)
        for arg in bad:
            print(f"Not KIND:ID:PATH=clean or =text: {arg}", file=sys.stderr)
        return 2
    tmdb = Tmdb(key)
    readings = json.loads(OUT.read_text()) if OUT.exists() else []
    known = {r["image"] for r in readings}
    for ref in filter(None, refs):
        kind, tid, path, expect = ref.groups()
        if path in known:
            continue
        details = tmdb.details("movie" if kind == "movie" else "tv", int(tid))
        name = str(details.get("title") or details.get("name"))
        titles = list(dict.fromkeys([name, *tmdb.all_titles("movie" if kind == "movie" else "tv", int(tid))]))
        lines = ocr.read(Tmdb.image(path))
        readings.append({
            "name": name, "titles": titles, "image": path, "expect": expect,
            "lines": [[line.text, round(line.score, 4), round(line.height, 4), round(line.width, 4)] for line in lines],
        })  # fmt: skip
        save(readings)
        print(f"{name} {path}: {len(lines)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
