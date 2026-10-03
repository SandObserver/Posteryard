from PIL import Image, ImageDraw, ImageFilter, ImageFont

from posteryard.quality import Badge
from posteryard.render import inforow
from posteryard.render.layers import (
    APPLE_BOTTOM,
    NEAR_BLACK,
    WHITE,
    cover,
    draw_tracked,
    font,
    mark,
    radial_shade,
    tracked_width,
    vertical_gradient,
)

POSTER = (1000, 1500)
WIDE = (1920, 1080)

LOGO_BOX = (0.687, 0.151)
LOGO_CENTRE = 0.755
LOGO_CENTRE_WITH_CAPTION = 0.72
CAPTION_SIZE = 13 / 219
CAPTION_Y = 0.845
SERVICE_HEIGHT = 0.054
SERVICE_MAX_WIDTH = 0.2
SERVICE_MARGIN = 0.044


def _service(canvas: Image.Image, service: str) -> None:
    w, h = canvas.size
    margin = round(SERVICE_MARGIN * w)
    logo = mark(service, round(SERVICE_HEIGHT * w))
    max_w = round(SERVICE_MAX_WIDTH * w)
    if logo.width > max_w:
        logo = logo.resize((max_w, max(1, round(logo.height * max_w / logo.width))), Image.Resampling.LANCZOS)
    x, y = w - margin - logo.width, margin
    canvas.alpha_composite(
        radial_shade(
            canvas.size, (w - margin, margin), (max(logo.width * 2.2, w * 0.4), max(logo.height * 4, h * 0.14))
        )
    )
    canvas.alpha_composite(logo, (x, y))


def tile_poster(
    art: Image.Image,
    logo: Image.Image,
    *,
    caption: str | None,
    badges: list[Badge],
    leaving: str | None,
    service: str | None,
) -> Image.Image:
    canvas = cover(art, *POSTER).convert("RGBA")
    w, h = canvas.size
    canvas.alpha_composite(vertical_gradient(canvas.size, APPLE_BOTTOM))
    logo = logo.convert("RGBA")
    scale = min(LOGO_BOX[0] * w / logo.width, LOGO_BOX[1] * h / logo.height)
    logo = logo.resize(
        (max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.Resampling.LANCZOS
    )
    centre = (LOGO_CENTRE_WITH_CAPTION if caption else LOGO_CENTRE) * h
    canvas.alpha_composite(logo, ((w - logo.width) // 2, round(centre - logo.height / 2)))
    if caption:
        draw_tracked(
            ImageDraw.Draw(canvas),
            (w / 2, CAPTION_Y * h),
            caption,
            font("Regular", round(CAPTION_SIZE * h)),
            (*WHITE, 179),
            align="centre",
        )
    if service:
        _service(canvas, service)
    inforow.draw(canvas, badges, leaving)
    return canvas.convert("RGB")


def episode_still(still: Image.Image, number: int, title: str) -> Image.Image:
    image = cover(still, *WIDE, (0.5, 0.5))
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


def background(art: Image.Image) -> Image.Image:
    return cover(art, *WIDE, (0.5, 0.5))
