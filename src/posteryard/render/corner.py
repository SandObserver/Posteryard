"""The top-left corner block: quality badges on the top row, the Maintainerr label under them."""

from dataclasses import dataclass

from PIL import Image, ImageDraw

from posteryard.quality import Badge
from posteryard.render.badges import badge
from posteryard.render.layers import APPLE_RED, WHITE, draw_tracked, font, radial_shade, tracked_width

LABEL_TRACKING = 0.08


@dataclass(frozen=True)
class CornerLayout:
    pad_x: float = 0.048
    top: float = 0.052
    row: float = 0.046
    badge_gap: float = 0.018
    row_gap: float = 0.016


@dataclass(frozen=True)
class Placed:
    image: Image.Image | None
    text: str | None
    x: int
    y: int
    width: int
    height: int


DEFAULT = CornerLayout()


def layout(badges: list[Badge], leaving: str | None, width: int, spec: CornerLayout = DEFAULT) -> list[Placed]:
    """Badges always start the first row; the label takes the next row, or the first when there are no badges."""
    x0, y = round(spec.pad_x * width), round(spec.top * width)
    row = round(spec.row * width)
    placed: list[Placed] = []
    if badges:
        x = x0
        for kind in badges:
            img = badge(kind, row)
            placed.append(Placed(img, None, x, y + (row - img.height) // 2, img.width, img.height))
            x += img.width + round(spec.badge_gap * width)
        y += row + round(spec.row_gap * width)
    if leaving:
        face = font("SemiBold", round(row * 0.7))
        dot = round(row * 0.42)
        text_w = round(tracked_width(leaving, face, LABEL_TRACKING))
        placed.append(Placed(None, leaving, x0, y, dot + round(row * 0.36) + text_w, row))
    return placed


def draw(canvas: Image.Image, badges: list[Badge], leaving: str | None, spec: CornerLayout = DEFAULT) -> None:
    placed = layout(badges, leaving, canvas.width, spec)
    if not placed:
        return
    right = max(p.x + p.width for p in placed)
    bottom = max(p.y + p.height for p in placed)
    x0, y0 = placed[0].x, round(spec.top * canvas.width)
    radii = (max(right * 2.0, canvas.width * 0.55), max(bottom * 4.0, canvas.height * 0.45))
    canvas.alpha_composite(radial_shade(canvas.size, (x0, y0), radii))
    pen = ImageDraw.Draw(canvas)
    row = round(spec.row * canvas.width)
    for p in placed:
        if p.image is not None:
            canvas.alpha_composite(p.image, (p.x, p.y))
        elif p.text is not None:
            dot = round(row * 0.42)
            mid = p.y + row / 2
            pen.ellipse((p.x, mid - dot / 2, p.x + dot, mid + dot / 2), fill=(*APPLE_RED, 255))
            face = font("SemiBold", round(row * 0.7))
            draw_tracked(
                pen, (p.x + dot + round(row * 0.36), mid), p.text, face, (*WHITE, 245), tracking=LABEL_TRACKING
            )
