"""Record what OCR reads on real TMDB art, for tests/ocr_readings.json, or check every reading again.

Usage: TMDB_API_KEY=... uv run python tools/ocr_readings.py movie:603:/path.jpg=clean tv:1396:/path.jpg=text
       TMDB_API_KEY=... uv run python tools/ocr_readings.py --check

--check reads every saved image again with the installed OCR and names each one whose decision changed.
"""

import json
import os
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from posteryard import artwork, ocr
from posteryard.ocr import TextLine
from posteryard.tmdb import Tmdb

OUT = Path(__file__).resolve().parent.parent / "tests" / "ocr_readings.json"
REF = re.compile(r"^(movie|tv):(\d+):(/\S+)=(clean|text)$")


def save(readings: list[dict[str, object]]) -> None:
    OUT.write_text(
        json.dumps(sorted(readings, key=lambda r: (r["name"], r["image"])), ensure_ascii=False, indent=1) + "\n"
    )


def runtime() -> str:
    return " ".join(f"{name} {metadata.version(name)}" for name in ("rapidocr", "onnxruntime"))


def clean(lines: list[TextLine], titles: list[str]) -> bool:
    return artwork.rejection(lines, titles) is None


def check(readings: list[dict[str, Any]]) -> int:
    print(f"Reading {len(readings)} images with {runtime()}.")
    worse = 0
    for reading in readings:
        titles = [str(t) for t in reading["titles"]]
        before = clean([TextLine(*line) for line in reading["lines"]], titles)
        now = clean(ocr.read(Tmdb.image(str(reading["image"]))), titles)
        if now != before:
            right = (reading["expect"] == "clean") == now
            worse += not right
            change = f"{'clean' if before else 'text'} -> {'clean' if now else 'text'}"
            print(f"{reading['name']} {reading['image']}: {change}, {'now right' if right else 'now WRONG'}")
    print(f"{worse} decisions got worse.")
    return 1 if worse else 0


def main(args: list[str]) -> int:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if key and args == ["--check"]:
        return check(json.loads(OUT.read_text()))
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
            "name": name, "titles": titles, "image": path, "expect": expect, "runtime": runtime(),
            "lines": [[line.text, round(line.score, 4), round(line.height, 4), round(line.width, 4)] for line in lines],
        })  # fmt: skip
        save(readings)
        print(f"{name} {path}: {len(lines)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
