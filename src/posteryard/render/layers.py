import math
from collections.abc import Sequence
from functools import cache
from importlib import resources
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RGB = tuple[int, int, int]
Stops = Sequence[tuple[float, float]]

WHITE: RGB = (255, 255, 255)
NEAR_BLACK: RGB = (29, 29, 31)
APPLE_RED: RGB = (255, 69, 58)
APPLE_GREEN: RGB = (48, 209, 88)
APPLE_BLUE: RGB = (10, 132, 255)
APPLE_YELLOW: RGB = (255, 214, 10)

APPLE_BOTTOM: Stops = ((0.0, 0.0), (0.54, 0.0), (0.70, 0.40), (0.85, 0.65), (1.0, 0.75))
CORNER: Stops = ((0.0, 0.72), (0.2, 0.5), (0.4, 0.32), (0.6, 0.14), (0.8, 0.03), (1.0, 0.0))

ASSETS = resources.files("posteryard") / "assets"


@cache
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = ASSETS / "fonts" / f"Inter-{weight}.ttf"
    return ImageFont.truetype(str(Path(str(path))), size)


MARK_DIRS: list[Path] = []


def add_mark_dir(folder: Path) -> None:
    """Also look for marks in `folder`, after the built-in ones."""
    if folder not in MARK_DIRS:
        MARK_DIRS.append(folder)


@cache
def _mark(name: str) -> Image.Image:
    built_in = ASSETS / "marks" / f"{name}.png"
    if built_in.is_file():
        return Image.open(str(built_in)).convert("RGBA")
    for folder in MARK_DIRS:
        path = folder / f"{name}.png"
        if path.is_file():
            return Image.open(str(path)).convert("RGBA")
    raise FileNotFoundError(f"no mark named {name}")


def mark(name: str, height: int, ink: RGB = WHITE) -> Image.Image:
    src = _mark(name)
    out = Image.new("RGBA", src.size, (*ink, 255))
    out.putalpha(src.getchannel("A"))
    return out.resize((max(1, round(src.width * height / src.height)), height), Image.Resampling.LANCZOS)


def trim(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    box = image.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return image.crop(box) if box else image


def cover(image: Image.Image, width: int, height: int, focus: tuple[float, float] = (0.5, 0.4)) -> Image.Image:
    image = image.convert("RGB")
    scale = max(width / image.width, height / image.height)
    scaled = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.Resampling.LANCZOS)
    x = int(min(max(focus[0] * scaled.width - width / 2, 0), scaled.width - width))
    y = int(min(max(focus[1] * scaled.height - height / 2, 0), scaled.height - height))
    return scaled.crop((x, y, x + width, y + height))


def vertical_gradient(size: tuple[int, int], stops: Stops, rgb: RGB = (0, 0, 0)) -> Image.Image:
    width, height = size
    xs, alphas = zip(*stops, strict=True)
    column = (np.interp(np.linspace(0, 1, height), xs, alphas) * 255).astype(np.uint8)
    layer = Image.new("RGBA", size, (*rgb, 255))
    layer.putalpha(Image.fromarray(np.repeat(column[:, None], width, axis=1)))
    return layer


def radial_shade(
    size: tuple[int, int], centre: tuple[float, float], radii: tuple[float, float], stops: Stops = CORNER
) -> Image.Image:
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width]
    distance = np.hypot((xx - centre[0]) / radii[0], (yy - centre[1]) / radii[1])
    xs, alphas = zip(*stops, strict=True)
    alpha = (np.interp(distance, xs, alphas) * 255).astype(np.uint8)
    layer = Image.new("RGBA", size, (0, 0, 0, 255))
    layer.putalpha(Image.fromarray(alpha))
    return layer


def tracked_width(text: str, face: ImageFont.FreeTypeFont, tracking: float) -> float:
    return sum(face.getlength(ch) for ch in text) + tracking * face.size * max(0, len(text) - 1)


def draw_tracked(
    canvas: Image.Image,
    xy: tuple[float, float],
    text: str,
    face: ImageFont.FreeTypeFont,
    fill: tuple[int, ...],
    *,
    tracking: float = 0.0,
    align: str = "left",
) -> None:
    """`xy` sets the left edge, or the centre with align="centre", and the vertical middle of the line.

    Drawn on its own layer: ImageDraw on an RGBA canvas replaces pixels, so a translucent fill would turn opaque.
    """
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x, y = xy
    if align == "centre":
        x -= tracked_width(text, face, tracking) / 2
    for ch in text:
        draw.text((x, y), ch, font=face, fill=fill, anchor="lm")
        x += face.getlength(ch) + tracking * face.size
    canvas.alpha_composite(layer)


def luminance(rgb: np.ndarray) -> np.ndarray:
    c = rgb / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    result: np.ndarray = 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]
    return result


def mean_luminance(image: Image.Image) -> float:
    """Relative luminance averaged over the visible pixels."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[..., 3] / 255
    return float((luminance(rgba[..., :3]) * alpha).sum() / max(alpha.sum(), 1.0))
