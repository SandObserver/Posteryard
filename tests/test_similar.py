import numpy as np
from PIL import Image, ImageDraw

from posteryard import similar


def scene(seed: int, size: tuple[int, int] = (600, 900)) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", size, tuple(int(c) for c in rng.integers(0, 120, 3)))
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        x, y = int(rng.integers(0, size[0])), int(rng.integers(0, size[1]))
        r = int(rng.integers(40, 160))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=tuple(int(c) for c in rng.integers(0, 256, 3)))
    return image


def test_a_crop_is_the_same_picture_and_another_scene_is_not() -> None:
    wide = scene(1, (1600, 900))
    crop = wide.crop((500, 0, 1100, 900))
    other = scene(2, (600, 900))
    shared = similar.overlap(similar.histogram(wide), similar.histogram(crop))
    assert similar.same_picture(similar.thumb(wide), similar.thumb(crop), shared)
    shared = similar.overlap(similar.histogram(crop), similar.histogram(other))
    assert not similar.same_picture(similar.thumb(crop), similar.thumb(other), shared)


def test_grain_drawn_differently_is_still_the_same_picture() -> None:
    base = scene(3)
    rng = np.random.default_rng(4)
    noisy = np.asarray(base, dtype=np.int16) + rng.integers(-40, 40, (900, 600, 3))
    grainy = Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))
    shared = similar.overlap(similar.histogram(base), similar.histogram(grainy))
    assert similar.same_picture(similar.thumb(base), similar.thumb(grainy), shared)


def detailed(seed: int, size: tuple[int, int] = (600, 900)) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", size, tuple(int(c) for c in rng.integers(0, 120, 3)))
    draw = ImageDraw.Draw(image)
    for _ in range(400):
        x, y = int(rng.integers(0, size[0])), int(rng.integers(0, size[1]))
        w, h = int(rng.integers(6, 40)), int(rng.integers(6, 40))
        draw.rectangle((x, y, x + w, y + h), fill=tuple(int(c) for c in rng.integers(0, 256, 3)))
    return image


def test_a_zoomed_or_widened_version_has_the_same_details_and_another_picture_does_not() -> None:
    poster = detailed(5)
    zoomed = poster.crop((90, 135, 510, 765)).resize((600, 900))
    wide = Image.new("RGB", (1600, 900), (20, 20, 20))
    wide.paste(poster.resize((500, 750)), (900, 80))
    other = detailed(6)
    original = similar.features(poster)
    assert similar.same_details(original, similar.features(zoomed))
    assert similar.same_details(original, similar.features(wide))
    assert not similar.same_details(original, similar.features(other))
