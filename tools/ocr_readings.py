"""Record what OCR reads on real TMDB art, for tests/ocr_readings.json.

Usage: TMDB_API_KEY=... uv run python tools/ocr_readings.py movie:603:/path.jpg=clean tv:1396:/path.jpg=text
"""

import json
import os
import sys
from pathlib import Path

from posteryard import ocr
from posteryard.tmdb import Tmdb

OUT = Path(__file__).resolve().parent.parent / "tests" / "ocr_readings.json"
EXPECT = ("clean", "text")


def main(args: list[str]) -> int:
    tmdb = Tmdb(os.environ["TMDB_API_KEY"])
    readings = json.loads(OUT.read_text()) if OUT.exists() else []
    known = {r["image"] for r in readings}
    for arg in args:
        ref, expect = arg.rsplit("=", 1)
        kind, tid, path = ref.split(":", 2)
        if expect not in EXPECT:
            print(f"{arg}: expected one of {', '.join(EXPECT)}")
            return 2
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
        print(f"{name} {path}: {len(lines)} lines")
    OUT.write_text(
        json.dumps(sorted(readings, key=lambda r: (r["name"], r["image"])), ensure_ascii=False, indent=1) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
