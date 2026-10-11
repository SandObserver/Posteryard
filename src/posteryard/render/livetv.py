"""Live TV cards in the style of the service collection tile, and the channel logo in the streaming mark's corner."""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from posteryard.render import designs
from posteryard.render.layers import cover, trim, vertical_gradient

W, H = designs.POSTER
CORNER = 0.12
CHANNEL_BOX, CHANNEL_CENTRE = (0.72, 0.2), 0.42
BAND_LOGO_BOX = (0.6, 0.13)
PICTURE_TOP = 0.18
PICTURE_BLUR, PICTURE_DIM = 40, 0.7
BAR_SPREAD = 6.0


def logo_mark(image: Image.Image) -> Image.Image:
    """A transparent logo is cut to its shape. An opaque logo, such as a photo, gets rounded corners."""
    image = image.convert("RGBA")
    if int(np.asarray(image.getchannel("A")).min()) < 255:
        return trim(image)
    mask = Image.new("L", image.size, 0)
    radius = round(min(image.size) * CORNER)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    image.putalpha(mask)
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


def channel_card(logo: Image.Image, art: Image.Image | None = None) -> Image.Image:
    """With art from one of the channel's shows: the service collection tile. Without: the band alone."""
    if art is None:
        canvas = band(H).convert("RGBA")
        mark = _fit(logo, CHANNEL_BOX)
        canvas.alpha_composite(mark, ((W - mark.width) // 2, round(CHANNEL_CENTRE * H - mark.height / 2)))
        return canvas.convert("RGB")
    split = round(designs.CHANNEL_SEAM * H)
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(cover(art, W, split, (0.5, 0.25)), (0, 0))
    canvas.alpha_composite(vertical_gradient((W, split), designs.CHANNEL_TOP_SHADE), (0, 0))
    canvas.paste(band(H - split), (0, split))
    mark = _fit(logo, BAND_LOGO_BOX)
    canvas.alpha_composite(mark, ((W - mark.width) // 2, round((split + H) / 2 - mark.height / 2)))
    return canvas.convert("RGB")


def title_card(logo: Image.Image, title: Image.Image) -> Image.Image:
    return with_channel(designs.tile_poster(band(H), title, lines_below=[], fade=0), logo)


def trim_bars(image: Image.Image) -> Image.Image:
    """Cut flat bars of one colour from the edges, such as padding a guide added to fit its own shape."""
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    flat_rows = pixels.std(axis=1).max(axis=1) < BAR_SPREAD
    flat_cols = pixels.std(axis=0).max(axis=1) < BAR_SPREAD
    rows, cols = np.flatnonzero(~flat_rows), np.flatnonzero(~flat_cols)
    if not len(rows) or not len(cols):
        return image
    return image.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))


def picture_card(picture: Image.Image, title: Image.Image) -> Image.Image:
    """The guide's own picture, whole, over a blurred copy of itself, with the title in Posteryard's style."""
    picture = trim_bars(picture).convert("RGB")
    blurred = cover(picture, W, H).filter(ImageFilter.GaussianBlur(PICTURE_BLUR))
    canvas = Image.fromarray((np.asarray(blurred, dtype=np.float32) * PICTURE_DIM).astype(np.uint8), "RGB")
    height = min(round(W * picture.height / picture.width), round((1 - PICTURE_TOP) * H))
    canvas.paste(cover(picture, W, height), (0, round(PICTURE_TOP * H)))
    return designs.tile_poster(canvas, title, lines_below=[])
