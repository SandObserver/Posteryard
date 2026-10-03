import numpy as np
from PIL import Image, ImageDraw

from posteryard import composition


def logo(colour: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    return Image.new("RGBA", (600, 150), (*colour, 255))


def busy(size: tuple[int, int] = (1000, 1500)) -> Image.Image:
    rng = np.random.default_rng(1)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))


def test_calm_art_beats_busy_art() -> None:
    calm = Image.new("RGB", (1000, 1500), (30, 30, 40))
    assert composition.score(calm, logo()) < composition.score(busy(), logo())


def test_a_logo_that_matches_its_background_scores_worse() -> None:
    pale = Image.new("RGB", (1000, 1500), (235, 235, 235))
    dark = Image.new("RGB", (1000, 1500), (10, 10, 10))
    grey = logo((120, 120, 120))
    assert composition.score(dark, logo()) < composition.score(pale, logo((250, 250, 250)))
    assert composition.score(dark, grey) >= 0


def test_logo_area_sits_on_the_logo_line() -> None:
    left, top, right, bottom = composition.logo_area(logo())
    assert left < 0.5 < right
    assert top < 0.896 < bottom


def test_faces_in_the_logo_area_cost_more(monkeypatch: object) -> None:
    art = Image.new("RGB", (1000, 1500), (20, 20, 20))
    calm = composition.score(art, logo())
    w, h = composition.SAMPLE
    box = (w // 3, round(0.8 * h), w // 4, w // 4)
    import pytest  # noqa: PLC0415

    patch = pytest.MonkeyPatch()
    patch.setattr(composition, "faces", lambda image: [box])
    try:
        assert composition.score(art, logo()) > calm + 1
    finally:
        patch.undo()


def test_the_face_detector_finds_nothing_in_a_flat_image() -> None:
    assert composition.faces(np.asarray(Image.new("RGB", composition.SAMPLE, (40, 40, 40)))) == []


def test_logo_luminance_ignores_transparent_pixels() -> None:
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((0, 0, 4, 9), fill=(255, 255, 255, 255))
    assert composition.logo_luminance(image) > 0.99
