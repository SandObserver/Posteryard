import multiprocessing
import os
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw

from posteryard import ocr
from posteryard.ocr import TextLine
from posteryard.render.layers import font


def line(text: str, height: float = 0.05, width: float = 0.5, score: float = 0.99) -> TextLine:
    return TextLine(text, score, height, width)


def test_title_match_ignores_case_spacing_and_punctuation() -> None:
    assert ocr.shows_title([line("DUNE"), line("PART TWO")], ["Dune: Part Two"])
    assert ocr.shows_title([line("FLY ME TO"), line("THE MOON")], ["Fly Me to the Moon"])
    assert not ocr.shows_title([line("COMO VENDER A LUA")], ["Fly Me to the Moon"])
    assert not ocr.shows_title([line("THE OFFICE", score=0.3)], ["The Office"])


def test_display_text_is_large_text_only() -> None:
    assert ocr.has_display_text([line("A TAGLINE", height=0.06, width=0.6)])
    assert not ocr.has_display_text([line("WORLD'S BEST BOSS", height=0.02, width=0.2)])
    assert not ocr.has_display_text([line("8", height=0.8, width=0.75), line("福", height=0.6, width=0.8)])


def test_engine_reads_rendered_text() -> None:
    image = Image.new("RGB", (1000, 1500), (12, 12, 14))
    ImageDraw.Draw(image).text((500, 700), "the office", font=font("Bold", 120), fill=(240, 240, 240), anchor="mm")
    assert ocr.shows_title(ocr.read(image), ["The Office"])


def test_the_ocr_process_stops_when_idle_and_starts_again(monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (1000, 1500), (12, 12, 14))
    ImageDraw.Draw(image).text((500, 700), "the office", font=font("Bold", 120), fill=(240, 240, 240), anchor="mm")
    ocr.read(image)
    running = ocr._process.pool
    ocr.close_idle()
    assert running is not None and ocr._process.pool is running
    monkeypatch.setattr(ocr, "IDLE_SECONDS", 0)
    ocr.close_idle()
    assert ocr._process.pool is None
    assert ocr.shows_title(ocr.read(image), ["The Office"])


def stuck(pixels: np.ndarray) -> list[TextLine]:
    time.sleep(30)
    return []


def test_a_stuck_read_stops_the_ocr_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "_read_pixels", stuck)
    monkeypatch.setattr(ocr, "READ_TIMEOUT", 1)
    ocr._process.close()
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        ocr.read(Image.new("RGB", (480, 720)))
    assert ocr._process.pool is None
    assert time.monotonic() - started < 15
    assert not multiprocessing.active_children()


def test_onnxruntime_telemetry_is_off() -> None:
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
