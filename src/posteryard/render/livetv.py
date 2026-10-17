import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from posteryard.render import designs
from posteryard.render.layers import cover, trim

W, H = designs.POSTER
CORNER = 0.12
PICTURE_TOP = 0.18
PICTURE_BLUR, PICTURE_DIM = 40, 0.7
FROSTED_BOX, FROSTED_CENTRE = (0.62, 0.16), 0.36
FROSTED_GRID, FROSTED_BLUR, FROSTED_DIM, FROSTED_SHADE = (6, 9), 80, 0.5, 0.45
FROSTED_LIGHT_DIM, FROSTED_LIGHT_TINT, DARK_LOGO = 0.85, 0.35, 0.25
BAR_SPREAD = 6.0
# Covering the poster scales a thin picture by its aspect. A 1920x10 picture needs 1.7 GB.
PICTURE_MAX_ASPECT = 4.0


def logo_mark(image: Image.Image) -> Image.Image:
    """A transparent logo is cut to its shape. An opaque logo, such as a photo, gets rounded corners."""
    image = image.convert("RGBA")
    if int(np.asarray(image.getchannel("A")).min()) < 255:
        image = trim(image)
    else:
        mask = Image.new("L", image.size, 0)
        radius = round(min(image.size) * CORNER)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
        image.putalpha(mask)
    if int(np.asarray(image.getchannel("A")).max()) <= 8:
        raise ValueError("the logo is fully transparent")
    return image


def band(height: int) -> Image.Image:
    top, bottom = designs.CHANNEL_DEFAULT_BAND
    rows = np.linspace(np.array(top, float), np.array(bottom, float), height)[:, None, :].repeat(W, axis=1)
    return Image.fromarray(rows.astype(np.uint8), "RGB")


def _fit(logo: Image.Image, box: tuple[float, float]) -> Image.Image:
    scale = min(box[0] * W / logo.width, box[1] * H / logo.height)
    size = (max(1, round(logo.width * scale)), max(1, round(logo.height * scale)))
    return logo.resize(size, Image.Resampling.LANCZOS)


def with_channel(poster: Image.Image, logo: Image.Image) -> Image.Image:
    """The channel logo in its own colours, where a TV show poster carries its streaming mark."""
    aspect = logo.width / logo.height
    height = W * min(
        math.sqrt(designs.SERVICE_AREA / aspect), designs.SERVICE_MAX_HEIGHT, designs.SERVICE_MAX_WIDTH / aspect
    )
    mark = logo.resize((max(1, round(height * aspect)), max(1, round(height))), Image.Resampling.LANCZOS)
    canvas = poster.convert("RGBA")
    margin = round(designs.SERVICE_MARGIN * W)
    canvas.alpha_composite(mark, (margin, margin))
    return canvas.convert("RGB")


def dark_logo(logo: Image.Image) -> bool:
    rgba = np.asarray(logo.convert("RGBA"), dtype=np.float32) / 255
    alpha = rgba[..., 3]
    luminance = rgba[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return float((luminance * alpha).sum() / alpha.sum()) < DARK_LOGO


def title_card(logo: Image.Image | None, title: Image.Image) -> Image.Image:
    if logo is None:
        return designs.tile_poster(band(H), title, lines_below=[], fade=0)
    light = dark_logo(logo)
    flat = Image.new("RGBA", logo.size, (255, 255, 255, 255) if light else (0, 0, 0, 255))
    tint = logo.copy()
    if light:
        tint.putalpha(logo.getchannel("A").point(lambda v: round(v * FROSTED_LIGHT_TINT)))
    flat.alpha_composite(tint)
    tiny = cover(flat, *FROSTED_GRID).resize((W, H), Image.Resampling.BICUBIC)
    dim = FROSTED_LIGHT_DIM if light else FROSTED_DIM
    frosted = np.asarray(tiny.filter(ImageFilter.GaussianBlur(FROSTED_BLUR)), dtype=np.float32) * dim
    if not light:
        ys, xs = np.ogrid[:H, :W]
        distance = np.hypot((xs - W / 2) / (0.55 * W), (ys - FROSTED_CENTRE * H) / (0.18 * H))
        frosted *= (1 - FROSTED_SHADE * np.clip(1 - distance, 0, 1) ** 2)[..., None]
    canvas = Image.fromarray(frosted.astype(np.uint8), "RGB").convert("RGBA")
    mark = _fit(logo, FROSTED_BOX)
    canvas.alpha_composite(mark, ((W - mark.width) // 2, round(FROSTED_CENTRE * H - mark.height / 2)))
    return designs.tile_poster(canvas.convert("RGB"), title, lines_below=[])


def trim_bars(image: Image.Image) -> Image.Image:
    """Cut flat bars of one colour from the edges, such as padding a guide added to fit its own shape."""
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    flat_rows = pixels.std(axis=1).max(axis=1) < BAR_SPREAD
    flat_cols = pixels.std(axis=0).max(axis=1) < BAR_SPREAD
    rows, cols = np.flatnonzero(~flat_rows), np.flatnonzero(~flat_cols)
    if not len(rows) or not len(cols):
        return image
    return image.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))


def guide_picture(image: Image.Image) -> Image.Image:
    """The guide's picture without flat bars. A picture too thin for a poster is refused."""
    picture = trim_bars(image).convert("RGB")
    if max(picture.width / picture.height, picture.height / picture.width) > PICTURE_MAX_ASPECT:
        raise ValueError(f"the picture is {picture.width}x{picture.height}, too thin for a poster")
    return picture


def picture_card(picture: Image.Image, title: Image.Image) -> Image.Image:
    """The guide's own picture, whole, over a blurred copy of itself, with the title in Posteryard's style."""
    picture = guide_picture(picture)
    blurred = cover(picture, W, H).filter(ImageFilter.GaussianBlur(PICTURE_BLUR))
    canvas = Image.fromarray((np.asarray(blurred, dtype=np.float32) * PICTURE_DIM).astype(np.uint8), "RGB")
    height = min(round(W * picture.height / picture.width), round((1 - PICTURE_TOP) * H))
    canvas.paste(cover(picture, W, height), (0, round(PICTURE_TOP * H)))
    return designs.tile_poster(canvas, title, lines_below=[])
