"""Reference renders of every design. Set UPDATE_GOLDEN=1 to write them again after bumping DESIGN_VERSION."""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from posteryard.pipeline import DESIGN_VERSION
from posteryard.quality import Badge
from posteryard.render import category, designs, lines, livetv
from posteryard.render.layers import APPLE_GREEN, ASSETS

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


def logo(colour: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((20, 20, 380, 180), fill=(*colour, 255))
    return image


LIVE_RENDERS: dict[str, Callable[[], Image.Image]] = {
    "live-title-plain": lambda: livetv.title_card(None, designs.text_logo("Golden Example")),
    "live-title-frosted": lambda: livetv.title_card(logo((230, 60, 40)), designs.text_logo("Golden Example")),
    "live-title-frosted-light": lambda: livetv.title_card(logo((20, 20, 30)), designs.text_logo("Golden Example")),
    "live-picture": lambda: livetv.picture_card(art(1920, 1080), designs.text_logo("Golden Example")),
    "live-channel": lambda: livetv.with_channel(art(1000, 1500), livetv.logo_mark(logo((40, 120, 220)))),
}
DATA = GOLDEN / "drawing-data.json"


def digest(value: object) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def drawing_data() -> dict[str, str]:
    """Drawn tables and marks are not fingerprint inputs: a changed entry needs a DESIGN_VERSION bump."""
    found = {"band:default": digest(designs.CHANNEL_DEFAULT_BAND)}
    found |= {f"band:{name}": digest(value) for name, value in designs.CHANNEL_BANDS.items()}
    found |= {f"brand:{name}": digest(value) for name, value in designs.BRAND_COLOURS.items()}
    found |= {f"palette:{name}": digest(value) for name, value in category.PALETTES.items()}
    marks = (path for path in (ASSETS / "marks").iterdir() if path.name.endswith(".png"))
    found |= {f"mark:{path.name}": digest(path.read_bytes()) for path in marks}
    return found


def small(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize(SIZE if image.height > image.width else SIZE[::-1], Image.Resampling.BOX)


@pytest.mark.skipif(not os.environ.get("UPDATE_GOLDEN"), reason="UPDATE_GOLDEN is not set")
def test_write_golden_images() -> None:
    GOLDEN.mkdir(exist_ok=True)
    for name, render in (RENDERS | LIVE_RENDERS).items():
        small(render()).save(GOLDEN / f"{name}.png")
    (GOLDEN / "DESIGN_VERSION").write_text(DESIGN_VERSION + "\n")
    (GOLDEN / "LIVE_VERSION").write_text(livetv.LIVE_VERSION + "\n")
    DATA.write_text(json.dumps(drawing_data(), indent=1, sort_keys=True) + "\n")


def difference(name: str, render: Callable[[], Image.Image]) -> float:
    with Image.open(GOLDEN / f"{name}.png") as reference:
        expected = np.asarray(reference.convert("RGB"), dtype=np.float32)
    actual = np.asarray(small(render()), dtype=np.float32)
    return float(np.abs(actual - expected).mean())


@pytest.mark.parametrize("name", sorted(RENDERS))
def test_rendered_output_changes_only_with_design_version(name: str) -> None:
    recorded = (GOLDEN / "DESIGN_VERSION").read_text().strip()
    assert recorded == DESIGN_VERSION, "DESIGN_VERSION changed: run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    changed = difference(name, RENDERS[name])
    assert changed <= TOLERANCE, (
        f"{name} renders differently ({changed:.2f}). Bump DESIGN_VERSION in pipeline.py, "
        "then run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    )


@pytest.mark.parametrize("name", sorted(LIVE_RENDERS))
def test_live_tv_output_changes_only_with_a_version(name: str) -> None:
    recorded = ((GOLDEN / "DESIGN_VERSION").read_text().strip(), (GOLDEN / "LIVE_VERSION").read_text().strip())
    assert recorded == (DESIGN_VERSION, livetv.LIVE_VERSION), (
        "A version changed: run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    )
    changed = difference(name, LIVE_RENDERS[name])
    assert changed <= TOLERANCE, (
        f"{name} renders differently ({changed:.2f}). Bump LIVE_VERSION in render/livetv.py, "
        "then run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    )


def test_drawing_data_changes_only_with_design_version() -> None:
    assert (GOLDEN / "DESIGN_VERSION").read_text().strip() == DESIGN_VERSION
    recorded: dict[str, str] = json.loads(DATA.read_text())
    current = drawing_data()
    changed = sorted(name for name, value in recorded.items() if current.get(name) != value)
    assert not changed, (
        f"{', '.join(changed)} changed or went away. Bump DESIGN_VERSION in pipeline.py, "
        "then run UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    )
