from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

from posteryard.quality import Badge
from posteryard.render.badges import badge
from posteryard.render.layers import RGB, WHITE, draw_tracked, font, tracked_width

LOGO_ALONE = 0.896
LOGO_ABOVE_LINE = 0.077
LAST_LINE = 0.905
CAPTION_PITCH = 0.056
BADGE_PITCH = 0.046

CAPTION_SIZE = 0.053
CAPTION_ALPHA = 140
LABEL_SIZE = 0.026
LABEL_DOT = 0.015
LABEL_GAP = 0.010
LABEL_TRACKING = 0.08
LABEL_ALPHA = 200
LABEL_ABOVE = 0.032

BADGE_ROW = 0.042
BADGE_GAP = 0.018
MAX_ROW = 0.80


@dataclass(frozen=True)
class Label:
    text: str
    colour: RGB


@dataclass(frozen=True)
class Caption:
    text: str


@dataclass(frozen=True)
class Badges:
    kinds: tuple[Badge, ...]


Line = Caption | Badges
PITCH: dict[type, float] = {Caption: CAPTION_PITCH, Badges: BADGE_PITCH}


def stack(lines: Sequence[Line]) -> tuple[float, list[float]]:
    centres: list[float] = []
    y = LAST_LINE
    for line in reversed(lines):
        centres.insert(0, y)
        y -= PITCH[type(line)]
    return (centres[0] - LOGO_ABOVE_LINE if centres else LOGO_ALONE), centres


def draw(canvas: Image.Image, lines: Sequence[Line], centres: Sequence[float], ink: RGB = WHITE) -> None:
    for line, centre in zip(lines, centres, strict=True):
        y = centre * canvas.height
        match line:
            case Caption(text):
                face = font("Regular", round(CAPTION_SIZE * canvas.height))
                draw_tracked(canvas, (canvas.width / 2, y), text, face, (*ink, CAPTION_ALPHA), align="centre")
            case Badges(kinds):
                _badges(canvas, kinds, y, ink)


def draw_label_above(canvas: Image.Image, label: Label, logo_top: int, ink: RGB = WHITE) -> None:
    _label(canvas, label.text, label.colour, logo_top - LABEL_ABOVE * canvas.height, ink)


def _label(canvas: Image.Image, text: str, colour: RGB, y: float, ink: RGB = WHITE) -> None:
    h = canvas.height
    face = font("SemiBold", round(LABEL_SIZE * h))
    dot, gap = round(LABEL_DOT * h), round(LABEL_GAP * h)
    width = dot + gap + tracked_width(text, face, LABEL_TRACKING)
    x = (canvas.width - width) / 2
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((x, y - dot / 2, x + dot, y + dot / 2), fill=(*colour, 255))
    canvas.alpha_composite(layer)
    draw_tracked(canvas, (x + dot + gap, y), text, face, (*ink, LABEL_ALPHA), tracking=LABEL_TRACKING)


def _row(kinds: Sequence[Badge], height: int, gap: int, ink: RGB) -> tuple[list[Image.Image], int]:
    images = [badge(kind, height, ink) for kind in kinds]
    return images, sum(i.width for i in images) + gap * (len(images) - 1)


def _badges(canvas: Image.Image, kinds: Sequence[Badge], y: float, ink: RGB) -> None:
    w = canvas.width
    height, gap = round(BADGE_ROW * w), round(BADGE_GAP * w)
    images, total = _row(kinds, height, gap, ink)
    if total > MAX_ROW * w:
        scale = MAX_ROW * w / total
        height, gap = round(height * scale), round(gap * scale)
        images, total = _row(kinds, height, gap, ink)
    x = (w - total) // 2
    for image in images:
        canvas.alpha_composite(image, (x, round(y - image.height / 2)))
        x += image.width + gap
