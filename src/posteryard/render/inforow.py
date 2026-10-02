"""The row under the title logo: quality badges at the bottom, the Maintainerr label above them."""

from dataclasses import dataclass

from PIL import Image, ImageDraw

from posteryard.quality import Badge
from posteryard.render.badges import badge
from posteryard.render.layers import APPLE_RED, WHITE, draw_tracked, font, tracked_width

LABEL_TRACKING = 0.08


@dataclass(frozen=True)
class RowLayout:
    """Sizes as fractions of the poster width; `bottom` is the badge row's centre as a fraction of the height."""

    row: float = 0.046
    badge_gap: float = 0.018
    row_gap: float = 0.016
    bottom: float = 0.945


DEFAULT = RowLayout()


@dataclass(frozen=True)
class Placed:
    image: Image.Image | None
    text: str | None
    x: int
    y: int
    width: int
    height: int


def layout(badges: list[Badge], leaving: str | None, size: tuple[int, int], spec: RowLayout = DEFAULT) -> list[Placed]:
    """Badges keep one spot on every poster. The label sits above them, or in their spot when there are none."""
    width, height = size
    row = round(spec.row * width)
    centre_y = round(spec.bottom * height)
    placed: list[Placed] = []
    if badges:
        images = [badge(kind, row) for kind in badges]
        gap = round(spec.badge_gap * width)
        x = (width - (sum(i.width for i in images) + gap * (len(images) - 1))) // 2
        for img in images:
            placed.append(Placed(img, None, x, centre_y - img.height // 2, img.width, img.height))
            x += img.width + gap
        centre_y -= row + round(spec.row_gap * width)
    if leaving:
        face = font("SemiBold", round(row * 0.7))
        dot = round(row * 0.42)
        label_w = dot + round(row * 0.36) + round(tracked_width(leaving, face, LABEL_TRACKING))
        placed.append(Placed(None, leaving, (width - label_w) // 2, centre_y - row // 2, label_w, row))
    return placed


def draw(canvas: Image.Image, badges: list[Badge], leaving: str | None, spec: RowLayout = DEFAULT) -> None:
    pen = ImageDraw.Draw(canvas)
    row = round(spec.row * canvas.width)
    for p in layout(badges, leaving, canvas.size, spec):
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
