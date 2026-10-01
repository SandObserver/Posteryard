"""Quality badges in Apple's style: brand marks for Dolby, text boxes for the rest."""

from PIL import Image, ImageDraw

from posteryard.quality import Badge
from posteryard.render.layers import WHITE, font, mark

TEXT = {
    Badge.UHD: "4K",
    Badge.HD: "HD",
    Badge.HDR10: "HDR10",
    Badge.HDR10_PLUS: "HDR10+",
    Badge.DTS_X: "DTS:X",
    Badge.SURROUND_7_1: "7.1",
    Badge.SURROUND_5_1: "5.1",
}
MARKS = {Badge.DOLBY_VISION: "dolbyvision", Badge.DOLBY_ATMOS: "dolbyatmos"}
FILLED = frozenset({Badge.UHD})
MARK_SCALE = 0.78


def text_box(label: str, height: int, filled: bool) -> Image.Image:
    face = font("Bold", round(height * 0.6))
    pad = round(height * 0.32)
    width = round(face.getlength(label)) + 2 * pad
    stroke = max(2, round(height * 0.075))
    radius = round(height * 0.22)
    box = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(box)
    if filled:
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=(*WHITE, 255))
        text_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(text_mask).text((width / 2, height / 2), label, font=face, fill=255, anchor="mm")
        box.putalpha(_knock(box, text_mask))
    else:
        inset = stroke / 2
        draw.rounded_rectangle(
            (inset, inset, width - 1 - inset, height - 1 - inset), radius=radius, outline=(*WHITE, 255), width=stroke
        )
        draw.text((width / 2, height / 2), label, font=face, fill=(*WHITE, 255), anchor="mm")
    return box


def _knock(box: Image.Image, text_mask: Image.Image) -> Image.Image:
    alpha = box.getchannel("A")
    return Image.composite(Image.new("L", box.size, 0), alpha, text_mask)


def badge(kind: Badge, height: int) -> Image.Image:
    if kind in MARKS:
        return mark(MARKS[kind], round(height * MARK_SCALE))
    return text_box(TEXT[kind], height, kind in FILLED)
