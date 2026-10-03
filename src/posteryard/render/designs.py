import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from posteryard.render import lines
from posteryard.render.layers import (
    APPLE_BOTTOM,
    CORNER,
    NEAR_BLACK,
    WHITE,
    cover,
    font,
    luminance,
    mark,
    radial_shade,
    tracked_width,
    trim,
    vertical_gradient,
)

POSTER = (1000, 1500)
WIDE = (1920, 1080)

LOGO_BOX = (0.66, 0.151)
# Apple's Top 10 rank digit: left and top edges, cap height, and white fading in the lower half.
NUMBER_AT = (0.07, 0.05)
NUMBER_CAP = 0.12
NUMBER_FADE = ((0.0, 0.90), (0.5, 0.90), (1.0, 0.53))
# Apple TV's "Explore Channels" tile, measured on Paramount+, Disney+, Crave, Prime Video and Gem.
CHANNEL_SEAM = 0.627
CHANNEL_TOP_SHADE = ((0.0, 0.45), (0.22, 0.0), (1.0, 0.0))
CHANNEL_LOGO_BOX = (0.42, 0.075)
CHANNEL_LOGO_TOP = 0.045
# Service marks cover the same area, as a fraction of the tile; compact marks stop at the height cap.
CHANNEL_MARK_AREA = 0.067
CHANNEL_MARK_MAX_HEIGHT = 0.12
CHANNEL_MARK_MAX_WIDTH = 0.77
CHANNEL_DEFAULT_BAND = ((44, 44, 48), (28, 28, 30))
CHANNEL_BANDS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "paramountplus": ((47, 86, 186), (54, 94, 196)),
    "disney": ((58, 95, 96), (76, 120, 118)),
    "crave": ((65, 60, 80), (50, 54, 74)),
    "prime": ((35, 88, 200), (34, 84, 183)),
    "netflix": ((150, 12, 20), (128, 8, 16)),
    "appletv": ((44, 44, 48), (28, 28, 30)),
    "hbomax": ((38, 52, 178), (32, 42, 150)),
    "hulu": ((24, 120, 80), (20, 100, 68)),
    "peacock": ((40, 40, 44), (24, 24, 26)),
    "youtube": ((170, 24, 24), (142, 18, 18)),
}
PLAIN_STILL = ((0.0, 0.0), (0.65, 0.0), (1.0, 0.35))
NUMBER_SHADE: tuple[tuple[float, float], ...] = ((0.0, 0.40), (0.5, 0.20), (1.0, 0.0))
SERVICE_HEIGHT = 0.054
SERVICE_MAX_HEIGHT = 0.085
SERVICE_MAX_WIDTH = 0.2
SERVICE_MARGIN = 0.044
# Every mark covers the area of a 3.7:1 mark at SERVICE_HEIGHT, so stacked and wide marks look the same size.
SERVICE_AREA = SERVICE_HEIGHT**2 * 3.7
SERVICE_CONTRAST = 4.5
SERVICE_SHADE_STEPS = (1.0, 1.2, 1.4, 1.6, 1.8)
SERVICE_SHADE_MAX = 0.9


def _service_size(service: str, width: int) -> tuple[int, int]:
    src = mark(service, 240)
    aspect = src.width / src.height
    height = min(math.sqrt(SERVICE_AREA / aspect), SERVICE_MAX_HEIGHT, SERVICE_MAX_WIDTH / aspect) * width
    return max(1, round(height * aspect)), max(1, round(height))


def _contrast_behind(canvas: Image.Image, logo: Image.Image, at: tuple[int, int]) -> float:
    """WCAG contrast of white against the brightest tenth of the pixels under the mark."""
    behind = np.asarray(canvas.crop((*at, at[0] + logo.width, at[1] + logo.height)).convert("RGB"), dtype=np.float32)
    covered = np.asarray(logo.getchannel("A")) > 127
    if not covered.any():
        return 21.0
    lum = float(np.percentile(luminance(behind[covered]), 90))
    return 1.05 / (lum + 0.05)


def _service_logo(service: str, width: int) -> Image.Image:
    logo_w, logo_h = _service_size(service, width)
    return mark(service, logo_h).resize((logo_w, logo_h), Image.Resampling.LANCZOS)


def _shade_for(canvas: Image.Image, logo: Image.Image, at: tuple[int, int]) -> Image.Image:
    """The lightest corner shade that gives the white mark SERVICE_CONTRAST, or the darkest step."""
    w, h = canvas.size
    centre = (at[0] + logo.width / 2, at[1] + logo.height / 2)
    radii = (max(logo.width * 1.8, w * 0.4), max(logo.height * 4, h * 0.14))
    shaded = canvas
    for strength in SERVICE_SHADE_STEPS:
        stops = [(x, min(a * strength, SERVICE_SHADE_MAX)) for x, a in CORNER]
        shaded = canvas.copy()
        shaded.alpha_composite(radial_shade(canvas.size, centre, radii, stops))
        if _contrast_behind(shaded, logo, at) >= SERVICE_CONTRAST:
            break
    return shaded


def _service(canvas: Image.Image, service: str) -> None:
    margin = round(SERVICE_MARGIN * canvas.width)
    logo = _service_logo(service, canvas.width)
    canvas.paste(_shade_for(canvas, logo, (margin, margin)))
    canvas.alpha_composite(logo, (margin, margin))


def _season_number(canvas: Image.Image, number: int) -> None:
    w, h = canvas.size
    canvas.alpha_composite(radial_shade(canvas.size, (0, 0), (0.58 * w, 0.42 * h), NUMBER_SHADE))
    glyphs = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glyphs).text(
        (w // 4, h // 4), str(number), font=font("Bold", round(NUMBER_CAP * h / 0.727)), fill=255
    )
    box = glyphs.getbbox()
    if box is None:
        return
    glyphs = glyphs.crop(box)
    xs, alphas = zip(*NUMBER_FADE, strict=True)
    fade = np.interp(np.linspace(0, 1, glyphs.height), xs, alphas)[:, None]
    layer = Image.new("RGBA", glyphs.size, (*WHITE, 0))
    layer.putalpha(Image.fromarray((np.asarray(glyphs, dtype=np.float32) * fade).astype(np.uint8)))
    canvas.alpha_composite(layer, (round(NUMBER_AT[0] * w), round(NUMBER_AT[1] * h)))


def tile_poster(
    art: Image.Image,
    logo: Image.Image,
    *,
    lines_below: list[lines.Line],
    label: lines.Label | None = None,
    number: int | None = None,
    service: str | None = None,
) -> Image.Image:
    canvas = cover(art, *POSTER).convert("RGBA")
    w, h = canvas.size
    canvas.alpha_composite(vertical_gradient(canvas.size, APPLE_BOTTOM))
    logo = logo.convert("RGBA")
    scale = min(LOGO_BOX[0] * w / logo.width, LOGO_BOX[1] * h / logo.height)
    logo = logo.resize(
        (max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.Resampling.LANCZOS
    )
    logo_bottom, centres = lines.stack(lines_below)
    logo_top = round(logo_bottom * h) - logo.height
    canvas.alpha_composite(logo, ((w - logo.width) // 2, logo_top))
    lines.draw(canvas, lines_below, centres)
    if label is not None:
        lines.draw_label_above(canvas, label, logo_top)
    if number is not None:
        _season_number(canvas, number)
    if service:
        _service(canvas, service)
    return canvas.convert("RGB")


def episode_still(still: Image.Image, number: int, title: str | None) -> Image.Image:
    """Without a title, the still with Apple's light bottom shade only. Plex prints the episode details beside it."""
    image = cover(still, *WIDE, (0.5, 0.5))
    if title is None:
        shaded = image.convert("RGBA")
        shaded.alpha_composite(vertical_gradient(shaded.size, PLAIN_STILL))
        return shaded.convert("RGB")
    w, h = image.size
    colour = image.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    assert isinstance(colour, tuple)
    ramp = Image.linear_gradient("L").resize((w, h))
    mask = ramp.point(lambda v: round(min(max((v / 255 - 0.68) / 0.22, 0.0), 1.0) * 255))
    image = Image.composite(image.filter(ImageFilter.GaussianBlur(w * 0.022)), image, mask)
    image = Image.composite(Image.new("RGB", image.size, colour[:3]), image, mask.point(lambda v: round(v * 0.55)))
    ink = WHITE if sum(colour[:3]) / 3 < 150 else NEAR_BLACK
    pen = ImageDraw.Draw(image)
    x = round(0.031 * w)
    pen.text(
        (x, h - round(0.128 * h)), f"EPISODE {number}", font=font("SemiBold", round(0.0172 * w)), fill=ink, anchor="lm"
    )
    face = font("SemiBold", round(0.0297 * w))
    pen.text((x, h - round(0.072 * h)), _fit(title, face, w - 2 * x), font=face, fill=ink, anchor="lm")
    return image


def _fit(text: str, face: ImageFont.FreeTypeFont, max_width: int) -> str:
    if tracked_width(text, face, 0) <= max_width:
        return text
    while text and tracked_width(text + "…", face, 0) > max_width:
        text = text[:-1].rstrip()
    return text + "…"


def channel_tile(art: Image.Image, logo: Image.Image | None, service: str) -> Image.Image:
    """Apple TV's channel tile: art with the featured title's logo on top, over a band with the service mark."""
    w, h = POSTER
    split = round(CHANNEL_SEAM * h)
    canvas = Image.new("RGBA", POSTER)
    canvas.paste(cover(art, w, split, (0.5, 0.25)), (0, 0))
    canvas.alpha_composite(vertical_gradient((w, split), CHANNEL_TOP_SHADE), (0, 0))
    if logo is not None:
        logo = logo.convert("RGBA")
        scale = min(CHANNEL_LOGO_BOX[0] * w / logo.width, CHANNEL_LOGO_BOX[1] * h / logo.height)
        logo = logo.resize(
            (max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.Resampling.LANCZOS
        )
        canvas.alpha_composite(logo, ((w - logo.width) // 2, round(CHANNEL_LOGO_TOP * h)))
    top, bottom = CHANNEL_BANDS.get(service, CHANNEL_DEFAULT_BAND)
    band = np.linspace(np.array(top, float), np.array(bottom, float), h - split)[:, None, :].repeat(w, axis=1)
    canvas.paste(Image.fromarray(band.astype(np.uint8), "RGB"), (0, split))
    src = mark(service, 400)
    aspect = src.width / src.height
    height = min(
        math.sqrt(CHANNEL_MARK_AREA * w / h / aspect), CHANNEL_MARK_MAX_HEIGHT, CHANNEL_MARK_MAX_WIDTH * w / h / aspect
    )
    sign = mark(service, max(1, round(height * h)))
    canvas.alpha_composite(sign, ((w - sign.width) // 2, round((split + h) / 2 - sign.height / 2)))
    return canvas.convert("RGB")


def text_logo(title: str) -> Image.Image:
    """The title set in white, for titles TMDB has no logo for: one line, or the two-line split that sets it largest."""
    words = title.split() or [title]
    options = [[" ".join(words)]]
    for cut in range(1, len(words)):
        options.append([" ".join(words[:cut]), " ".join(words[cut:])])
    face = font("Bold", 200)
    box = (LOGO_BOX[0] * POSTER[0], LOGO_BOX[1] * POSTER[1])

    def scale(rows: list[str]) -> float:
        width = max(face.getlength(row) for row in rows)
        return min(box[0] / width, box[1] / (len(rows) * face.size * 1.1))

    rows = max(options, key=scale)
    width = round(max(face.getlength(row) for row in rows)) + 20
    height = round(len(rows) * face.size * 1.1) + 20
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    for i, row in enumerate(rows):
        pen.text((width / 2, 10 + (i + 0.5) * face.size * 1.1), row, font=face, fill=(*WHITE, 255), anchor="mm")
    return trim(layer)


def background(art: Image.Image) -> Image.Image:
    return cover(art, *WIDE, (0.5, 0.5))
