"""Reference renders of every design. Set UPDATE_GOLDEN=1 to write them again after bumping DESIGN_VERSION."""

import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from posteryard.pipeline import DESIGN_VERSION
from posteryard.quality import Badge
from posteryard.render import category, designs, lines
from posteryard.render.layers import APPLE_GREEN

GOLDEN = Path(__file__).parent / "golden"
SIZE = (120, 180)
# Mean absolute difference per channel, out of 255. Font rasteriser updates stay below it; layout changes do not.
TOLERANCE = 1.0


def art(width: int, height: int) -> Image.Image:
    y, x = np.mgrid[0:height, 0:width]
    pixels = np.stack([(x * 255 // width), (y * 255 // height), ((x // 40 + y // 40) % 2) * 160 + 40], axis=-1).astype(
        np.uint8
    )
    return Image.fromarray(pixels, "RGB")


RENDERS: dict[str, Callable[[], Image.Image]] = {
    "tile": lambda: designs.tile_poster(
        art(2000, 3000),
        designs.text_logo("Golden Example"),
        lines_below=[
            lines.Caption("Specials"),
            lines.Badges((Badge.UHD, Badge.DOLBY_VISION, Badge.DOLBY_ATMOS)),
            lines.Badges((Badge.SDH,)),
        ],
        label=lines.Label("JUST ADDED", APPLE_GREEN),
        number=2,
        service="netflix",
    ),
    "episode-titled": lambda: designs.episode_still(art(1920, 1080), 3, "A Golden Episode"),
    "episode-plain": lambda: designs.episode_still(art(1920, 1080), 3, None),
    "channel": lambda: designs.channel_tile(art(2000, 3000), designs.text_logo("Golden Example"), "hbomax"),
    "background": lambda: designs.background(art(2400, 1200)),
    "category": lambda: category.category_tile(art(2000, 3000), "Golden Example Collection"),
}


def small(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize(SIZE if image.height > image.width else SIZE[::-1], Image.Resampling.BOX)


@pytest.mark.skipif(not os.environ.get("UPDATE_GOLDEN"), reason="UPDATE_GOLDEN is not set")
def test_write_golden_images() -> None:
    GOLDEN.mkdir(exist_ok=True)
    for name, render in RENDERS.items():
        small(render()).save(GOLDEN / f"{name}.png")
    (GOLDEN / "DESIGN_VERSION").write_text(DESIGN_VERSION + "\n")


@pytest.mark.parametrize("name", sorted(RENDERS))
def test_rendered_output_changes_only_with_design_version(name: str) -> None:
    recorded = (GOLDEN / "DESIGN_VERSION").read_text().strip()
    assert recorded == DESIGN_VERSION, "DESIGN_VERSION changed: run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    with Image.open(GOLDEN / f"{name}.png") as reference:
        expected = np.asarray(reference.convert("RGB"), dtype=np.float32)
    actual = np.asarray(small(RENDERS[name]()), dtype=np.float32)
    difference = float(np.abs(actual - expected).mean())
    assert difference <= TOLERANCE, (
        f"{name} renders differently ({difference:.2f}). Bump DESIGN_VERSION in pipeline.py, "
        "then run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    )
