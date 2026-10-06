"""Apple TV's category tile: the art recoloured in one palette, with the name bottom left."""

import hashlib
import math
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from posteryard.render.layers import cover, font_for, luminance

POSTER = (1000, 1500)
# Measured on the Apple TV app's Browse by Genre tiles: (angle, start tones, end tones).
# Tones are shadow, low, mid and highlight. The start tones turn into the end tones along the angle.
PALETTES: dict[str, tuple[int, tuple[str, ...], tuple[str, ...]]] = {
    "kids & family": (45, ("#1c0a0b", "#63382c", "#ad7355", "#eac698"), ("#1c0a0b", "#8d3142", "#d05e73", "#f7b0b9")),
    "action": (30, ("#160d06", "#54472e", "#82714b", "#e9ce8a"), ("#2b1308", "#4f240f", "#d85e25", "#f0b070")),
    "animation": (45, ("#170926", "#51225d", "#af51c1", "#f5b7f9"), ("#170926", "#6826b2", "#b84ee3", "#eb8cfc")),
    "comedy": (60, ("#06110e", "#133834", "#3a938c", "#66ecef"), ("#0e1c16", "#1a3426", "#3e8363", "#72dfb4")),
    "documentary": (135, ("#050b22", "#162a9f", "#2458c4", "#8fb6f2"), ("#0c1934", "#153467", "#2968bb", "#9cc2f0")),
    "drama": (150, ("#0f1010", "#2b2c2a", "#80806f", "#c1c7bc"), ("#0b0f11", "#203136", "#548fa6", "#9ac1ca")),
    "horror": (45, ("#1d0901", "#5e2708", "#c6742a", "#eca940"), ("#230d03", "#63280a", "#b65014", "#ed9f2d")),
    "reality": (15, ("#1a0b2f", "#4e2e49", "#985d8d", "#ecbee1"), ("#1e0e34", "#472073", "#b656e5", "#df8cf5")),
    "romance": (105, ("#120a0b", "#5f2f34", "#b26f76", "#e3abb2"), ("#13090a", "#5d2932", "#ab5460", "#e294a4")),
    "sci-fi": (30, ("#08141a", "#1e3742", "#60928e", "#a8d5d7"), ("#0e1b20", "#152931", "#377b8f", "#9ccfd6")),
    "sports": (60, ("#0a1830", "#1e3f7a", "#337ada", "#6ac5e8"), ("#0a1512", "#254239", "#50898b", "#b7e6b7")),
    "thriller": (30, ("#191716", "#313733", "#5f766d", "#a6bfb7"), ("#2e0d0d", "#421d1c", "#c65f5a", "#e0a49c")),
}
# Near-neutral palettes make other collections look like a black and white copy of the art.
SHARED = tuple(sorted(name for name in PALETTES if name not in ("drama", "thriller")))
STOPS = (0.0, 0.22, 0.45, 0.8, 1.0)
WHITE_HIGH = 0.35
LEVELS = (0.02, 0.92)
TARGET_MEDIAN = (0.28, 0.5)
LABEL_X, LABEL_BASE, LABEL_SIZE, LABEL_LINE, LABEL_WIDTH = 0.075, 0.925, 0.075, 0.085, 0.85
MIN_LABEL_SIZE = 0.045
MAX_ROWS = 3
CONTRAST = 4.5
SHADE_STEPS = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9)
SHADE_RISE = 0.16
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def palette_for(name: str) -> str:
    key = name.strip().lower()
    if key in PALETTES:
        return key
    return SHARED[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(SHARED)]


def _rgb(code: str) -> np.ndarray:
    return np.array([int(code[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255


def _levels(lum: np.ndarray) -> np.ndarray:
    """Stretch the art to the full range, then move its median to the middle, so light and dark art keep detail."""
    low, high = np.percentile(lum, (2, 98))
    out = np.clip((lum - low) / max(float(high - low), 0.05), 0, 1)
    median = float(np.median(out))
    target = min(max(median, TARGET_MEDIAN[0]), TARGET_MEDIAN[1])
    if 0.01 < median < 0.99 and median != target:
        out = out ** (math.log(target) / math.log(median))
    result: np.ndarray = LEVELS[0] + out * (LEVELS[1] - LEVELS[0])
    return result


def _map(lum: np.ndarray, tones: tuple[str, ...]) -> np.ndarray:
    colours = [_rgb(c) for c in tones]
    colours.append(colours[-1] + (1 - colours[-1]) * WHITE_HIGH)
    return np.stack([np.interp(lum, STOPS, [c[i] for c in colours]) for i in range(3)], axis=-1)


def _blend(w: int, h: int, angle: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = math.cos(math.radians(angle)) * xx / w + math.sin(math.radians(angle)) * yy / h
    t = (t - t.min()) / (t.max() - t.min())
    weight: np.ndarray = (t * t * (3 - 2 * t))[..., None]
    return weight


def _wide(word: str) -> bool:
    return any(unicodedata.east_asian_width(c) in ("W", "F") for c in word)


def _wrap(name: str, face: ImageFont.FreeTypeFont, width: float, *, split: bool) -> list[str]:
    """Chinese and Japanese words break between characters. Other words break only with split."""
    rows = [""]
    for word in name.split():
        trial = f"{rows[-1]} {word}".strip()
        if face.getlength(trial) <= width:
            rows[-1] = trial
        elif face.getlength(word) <= width or not (split or _wide(word)):
            if rows[-1]:
                rows.append(word)
            else:
                rows[-1] = word
        else:
            if rows[-1]:
                rows.append("")
            for char in word:
                if rows[-1] and face.getlength(rows[-1] + char) > width:
                    rows.append(char)
                else:
                    rows[-1] += char
    return rows


def _layout(name: str, w: int, h: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    width = LABEL_WIDTH * w
    size = LABEL_SIZE
    while True:
        face = font_for(name, "Bold", round(size * h))
        rows = _wrap(name, face, width, split=False)
        if len(rows) <= 2 and all(face.getlength(row) <= width for row in rows):
            return face, rows
        if size <= MIN_LABEL_SIZE:
            rows = _wrap(name, face, width, split=True)
            if len(rows) > MAX_ROWS:
                last = rows[MAX_ROWS - 1]
                while last and face.getlength(last.rstrip() + "…") > width:
                    last = last[:-1]
                rows = [*rows[: MAX_ROWS - 1], last.rstrip() + "…"]
            return face, rows
        size -= 0.005


def _right_to_left(text: str) -> bool:
    strong = next((c for c in text if unicodedata.bidirectional(c) in ("L", "R", "AL")), "")
    return bool(strong) and unicodedata.bidirectional(strong) in ("R", "AL")


def _contrast(behind: np.ndarray) -> float:
    """White text against the brightest tenth of what is behind it."""
    return 1.05 / (float(np.percentile(luminance(behind * 255), 90)) + 0.05)


def category_tile(art: Image.Image, name: str) -> Image.Image:
    w, h = POSTER
    base = np.asarray(cover(art, w, h).convert("RGB"), dtype=np.float32) / 255
    lum = _levels(base @ LUMA)
    angle, start, end = PALETTES[palette_for(name)]
    t = _blend(w, h, angle)
    out = _map(lum, start) * (1 - t) + _map(lum, end) * t
    face, rows = _layout(name, w, h)
    line = round(LABEL_LINE * face.size / LABEL_SIZE)
    rtl = _right_to_left(name)
    anchor = "rs" if rtl else "ls"
    x, base_y = round((1 - LABEL_X) * w if rtl else LABEL_X * w), round(LABEL_BASE * h)
    pad = round(0.02 * h)
    behind = np.zeros((h, w), dtype=bool)
    for i, row in enumerate(rows):
        left, top, right, bottom = face.getbbox(row, anchor=anchor)
        y = base_y - line * (len(rows) - 1 - i)
        behind[max(0, y + top - pad) : min(h, y + bottom + pad), max(0, x + left - pad) : min(w, x + right + pad)] = (
            True
        )
    if behind.any():
        text_top = int(np.nonzero(behind.any(axis=1))[0][0])
        shadow = (_rgb(start[0]) + _rgb(end[0])) / 2
        ramp = np.clip((np.arange(h, dtype=np.float32) - (text_top - SHADE_RISE * h)) / (SHADE_RISE * h), 0, 1)
        ramp = (ramp * ramp * (3 - 2 * ramp))[:, None, None]
        for strength in SHADE_STEPS:
            shaded = out * (1 - ramp * strength) + shadow * ramp * strength
            if _contrast(shaded[behind]) >= CONTRAST or strength == SHADE_STEPS[-1]:
                out = shaded
                break
    tile = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
    pen = ImageDraw.Draw(tile)
    y = base_y
    for row in reversed(rows):
        pen.text((x, y), row, font=face, fill=(255, 255, 255), anchor=anchor)
        y -= line
    return tile
