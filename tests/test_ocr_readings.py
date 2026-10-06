import json
from pathlib import Path
from typing import Any

import pytest

from posteryard import ocr
from posteryard.ocr import TextLine

READINGS: list[dict[str, Any]] = json.loads((Path(__file__).parent / "ocr_readings.json").read_text())


@pytest.mark.parametrize("reading", READINGS, ids=[f"{r['name']} {r['image']}" for r in READINGS])
def test_real_art_is_judged_as_expected(reading: dict[str, Any]) -> None:
    lines = [TextLine(*line) for line in reading["lines"]]
    clean = not ocr.shows_title(lines, reading["titles"]) and not ocr.has_display_text(lines)
    expected = reading["expect"] == "clean"
    assert clean == expected or (reading.get("known_miss") and not clean)


def test_readings_cover_clean_art_and_art_with_text() -> None:
    assert {r["expect"] for r in READINGS} == {"clean", "text"}
