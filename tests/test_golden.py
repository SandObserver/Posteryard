"""Reference renders of every design.

UPDATE_GOLDEN=1 writes every reference again. UPDATE_GOLDEN=tile,channel writes only those.
"""

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
# Font rasteriser and raqm differences stay under PIXEL_STEP. A removed label moves 49 pixels past it.
TOLERANCE = 1.0
PIXEL_STEP = 64
CHANGED_PIXELS = 10


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


def chosen() -> set[str]:
    names = os.environ.get("UPDATE_GOLDEN", "")
    return set(RENDERS | LIVE_RENDERS) if names == "1" else {name for name in names.split(",") if name}


@pytest.mark.skipif(not os.environ.get("UPDATE_GOLDEN"), reason="UPDATE_GOLDEN is not set")
def test_write_golden_images() -> None:
    unknown = chosen() - set(RENDERS | LIVE_RENDERS)
    assert not unknown, f"no design called {', '.join(sorted(unknown))}"
    GOLDEN.mkdir(exist_ok=True)
    for name, render in (RENDERS | LIVE_RENDERS).items():
        if name in chosen():
            small(render()).save(GOLDEN / f"{name}.png")
    (GOLDEN / "DESIGN_VERSION").write_text(DESIGN_VERSION + "\n")
    (GOLDEN / "LIVE_VERSION").write_text(livetv.LIVE_VERSION + "\n")
    DATA.write_text(json.dumps(drawing_data(), indent=1, sort_keys=True) + "\n")


def reference(name: str) -> Image.Image:
    with Image.open(GOLDEN / f"{name}.png") as image:
        return image.convert("RGB")


def gap(name: str, image: Image.Image) -> np.ndarray:
    return np.abs(np.asarray(image, dtype=np.int16) - np.asarray(reference(name), dtype=np.int16))


def difference(name: str, render: Callable[[], Image.Image]) -> tuple[float, int]:
    found = gap(name, small(render()))
    return float(found.mean()), int((found.max(axis=-1) > PIXEL_STEP).sum())


def assert_unchanged(name: str, render: Callable[[], Image.Image], folder: Path, bump: str) -> None:
    image = small(render())
    found = gap(name, image)
    mean, pixels = float(found.mean()), int((found.max(axis=-1) > PIXEL_STEP).sum())
    if mean <= TOLERANCE and pixels <= CHANGED_PIXELS:
        return
    reference(name).save(folder / "expected.png")
    image.save(folder / "actual.png")
    strongest = found.max(axis=-1)
    diff = np.repeat(np.clip(strongest * 4, 0, 255).astype(np.uint8)[..., None], 3, axis=-1)
    diff[strongest > PIXEL_STEP] = (255, 0, 0)
    Image.fromarray(diff, "RGB").save(folder / "diff.png")
    pytest.fail(
        f"{name} renders differently (mean {mean:.2f}, {pixels} pixels changed). Expected, actual and diff images "
        f"are in {folder}. {bump}, then run UPDATE_GOLDEN=1 uv run pytest --no-cov tests/test_golden.py"
    )


@pytest.mark.parametrize("name", sorted(RENDERS))
def test_rendered_output_changes_only_with_design_version(name: str, tmp_path: Path) -> None:
    recorded = (GOLDEN / "DESIGN_VERSION").read_text().strip()
    assert recorded == DESIGN_VERSION, (
        "DESIGN_VERSION changed: run UPDATE_GOLDEN=1 uv run pytest --no-cov tests/test_golden.py"
    )
    assert_unchanged(name, RENDERS[name], tmp_path, "Bump DESIGN_VERSION in pipeline.py")


@pytest.mark.parametrize("name", sorted(LIVE_RENDERS))
def test_live_tv_output_changes_only_with_a_version(name: str, tmp_path: Path) -> None:
    recorded = ((GOLDEN / "DESIGN_VERSION").read_text().strip(), (GOLDEN / "LIVE_VERSION").read_text().strip())
    assert recorded == (DESIGN_VERSION, livetv.LIVE_VERSION), (
        "A version changed: run UPDATE_GOLDEN=1 uv run pytest --no-cov tests/test_golden.py"
    )
    assert_unchanged(name, LIVE_RENDERS[name], tmp_path, "Bump LIVE_VERSION in render/livetv.py")


def test_a_removed_label_counts_as_a_change() -> None:
    def without_label() -> Image.Image:
        return designs.tile_poster(
            art(2000, 3000),
            designs.text_logo("Golden Example"),
            lines_below=[
                lines.Caption("Specials"),
                lines.Badges((Badge.UHD, Badge.DOLBY_VISION, Badge.DOLBY_ATMOS)),
                lines.Badges((Badge.SDH,)),
            ],
            number=2,
            service="netflix",
        )

    assert difference("tile", without_label)[1] > CHANGED_PIXELS


def test_a_failed_comparison_saves_the_images(tmp_path: Path) -> None:
    def opposite() -> Image.Image:
        return Image.eval(reference("tile"), lambda value: 0 if value > 127 else 255)

    with pytest.raises(pytest.fail.Exception, match=str(tmp_path)):
        assert_unchanged("tile", opposite, tmp_path, "Bump DESIGN_VERSION")
    assert {path.name for path in tmp_path.iterdir()} == {"expected.png", "actual.png", "diff.png"}
    with Image.open(tmp_path / "diff.png") as diff:
        assert diff.getcolors() == [(SIZE[0] * SIZE[1], (255, 0, 0))]


def test_drawing_data_changes_only_with_design_version() -> None:
    assert (GOLDEN / "DESIGN_VERSION").read_text().strip() == DESIGN_VERSION
    recorded: dict[str, str] = json.loads(DATA.read_text())
    current = drawing_data()
    added = (name for name in current.keys() - recorded.keys() if not name.startswith("mark:"))
    changed = sorted([*(name for name, value in recorded.items() if current.get(name) != value), *added])
    assert not changed, (
        f"{', '.join(changed)} changed, went away or is new. Bump DESIGN_VERSION in pipeline.py, "
        "then run UPDATE_GOLDEN=1 uv run pytest --no-cov tests/test_golden.py"
    )
