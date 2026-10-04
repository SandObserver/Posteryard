from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from posteryard import automarks
from posteryard.artwork import MemoryChoices
from posteryard.automarks import AutoMarks, cut
from posteryard.render import layers
from posteryard.services import Offer
from posteryard.tmdb import Tmdb


def flat_icon() -> Image.Image:
    icon = Image.new("RGB", (300, 300), (230, 20, 90))
    ImageDraw.Draw(icon).rectangle((60, 120, 240, 180), fill=(255, 255, 255))
    return icon


def gradient_icon() -> Image.Image:
    ramp = np.linspace(0, 255, 300, dtype=np.uint8)
    return Image.fromarray(np.stack([np.tile(ramp, (300, 1))] * 3, axis=-1))


def test_a_flat_icon_gives_a_white_mark_and_a_gradient_does_not() -> None:
    mark = cut(flat_icon())
    assert mark is not None
    assert mark.size == (181, 61)
    assert mark.getpixel((90, 30)) == (255, 255, 255, 255)
    assert cut(gradient_icon()) is None


def test_marks_are_cut_once_and_drawn_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def image(path: str) -> Image.Image:
        fetched.append(path)
        return flat_icon() if path == "/flat.jpg" else gradient_icon()

    monkeypatch.setattr(Tmdb, "image", staticmethod(image))
    marks = AutoMarks(tmp_path / "marks", MemoryChoices())
    name = marks.get(Offer(223, "Hayu", "/flat.jpg"))
    assert name == f"auto-223-v{automarks.VERSION}"
    assert marks.get(Offer(223, "Hayu", "/flat.jpg")) == name
    assert marks.get(Offer(9, "Gradient", "/gradient.jpg")) is None
    assert marks.get(Offer(9, "Gradient", "/gradient.jpg")) is None
    assert fetched == ["/flat.jpg", "/gradient.jpg"]
    assert layers.mark(name, 40).height == 40
    assert marks.get(Offer(0, "No id", "/flat.jpg")) is None
