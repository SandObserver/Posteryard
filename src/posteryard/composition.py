"""How well the title logo will read on a piece of art. Lower scores are better.

The art is cropped and shaded as the poster draws it, then the area the logo will cover is measured: edge detail,
faces in it, and the contrast between the logo's colour and what is behind it.
"""

from functools import cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from posteryard.render import designs, lines
from posteryard.render.layers import APPLE_BOTTOM, ASSETS, cover, luminance, vertical_gradient

SAMPLE = (400, 600)
MARGIN = 0.03
DETAIL_WEIGHT = 6.0
FACE_WEIGHT = 2.0
CONTRAST_TARGET = 3.0
CONTRAST_WEIGHT = 2.0
FACE_CONFIDENCE = 0.8
# CIE76 colour difference below which a logo blends into the art even when brightness differs.
HUE_TARGET = 30.0
HUE_WEIGHT = 1.0


@cache
def _detector() -> cv2.FaceDetectorYN:
    model = ASSETS / "models" / "face_detection_yunet_2023mar.onnx"
    return cv2.FaceDetectorYN.create(str(Path(str(model))), "", SAMPLE, FACE_CONFIDENCE)


def faces(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Face boxes as x, y, width, height, from YuNet on an RGB image of SAMPLE size."""
    detector = _detector()
    detector.setInputSize((image.shape[1], image.shape[0]))
    _, found = detector.detect(np.ascontiguousarray(image[..., ::-1]))
    if found is None:
        return []
    return [(int(f[0]), int(f[1]), int(f[2]), int(f[3])) for f in found]


def logo_area(logo: Image.Image) -> tuple[float, float, float, float]:
    """Left, top, right and bottom of the logo on a poster without lines, as fractions, plus a margin."""
    w, h = designs.POSTER
    scale = min(designs.LOGO_BOX[0] * w / logo.width, designs.LOGO_BOX[1] * h / logo.height)
    width, height = logo.width * scale / w, logo.height * scale / h
    bottom = lines.LOGO_ALONE
    return (
        max(0.0, 0.5 - width / 2 - MARGIN),
        max(0.0, bottom - height - MARGIN),
        min(1.0, 0.5 + width / 2 + MARGIN),
        min(1.0, bottom + MARGIN),
    )


def _overlap(box: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> float:
    """The share of the face that falls inside the logo area."""
    x, y, w, h = box
    ix = max(0, min(x + w, area[2]) - max(x, area[0]))
    iy = max(0, min(y + h, area[3]) - max(y, area[1]))
    return ix * iy / max(w * h, 1)


def _contrast(a: float, b: float) -> float:
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _lab(rgb: np.ndarray) -> np.ndarray:
    pixels = np.clip(rgb, 0, 255).astype(np.uint8).reshape(-1, 1, 3)
    lab: np.ndarray = cv2.cvtColor(pixels, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    scaled: np.ndarray = lab * np.array([100 / 255, 1, 1], dtype=np.float32) - np.array([0, 128, 128], dtype=np.float32)
    return scaled


def logo_colour(logo: Image.Image) -> np.ndarray:
    """The logo's average colour in Lab, over its visible pixels."""
    rgba = np.asarray(logo.convert("RGBA"), dtype=np.float32).reshape(-1, 4)
    shown = rgba[rgba[:, 3] > 127][:, :3]
    return _lab(shown).mean(axis=0) if len(shown) else np.zeros(3, dtype=np.float32)


def logo_luminance(logo: Image.Image) -> float:
    rgba = np.asarray(logo.convert("RGBA"), dtype=np.float32)
    alpha = rgba[..., 3] / 255
    return float((luminance(rgba[..., :3]) * alpha).sum() / max(alpha.sum(), 1.0))


def score(art: Image.Image, logo: Image.Image) -> float:
    plain = cover(art, *SAMPLE).convert("RGB")
    shaded = plain.convert("RGBA")
    shaded.alpha_composite(vertical_gradient(shaded.size, APPLE_BOTTOM))
    w, h = SAMPLE
    left, top, right, bottom = logo_area(logo)
    area = (round(left * w), round(top * h), round(right * w), round(bottom * h))

    grey = np.asarray(shaded.convert("L"), dtype=np.float32) / 255
    gx, gy = cv2.Sobel(grey, cv2.CV_32F, 1, 0), cv2.Sobel(grey, cv2.CV_32F, 0, 1)
    detail = float(np.hypot(gx, gy)[area[1] : area[3], area[0] : area[2]].mean())

    face = max((_overlap(box, area) for box in faces(np.asarray(plain))), default=0.0)

    behind = luminance(np.asarray(shaded.convert("RGB"), dtype=np.float32)[area[1] : area[3], area[0] : area[2]])
    contrast = _contrast(logo_luminance(logo), float(np.percentile(behind, 75)))
    shortfall = max(0.0, CONTRAST_TARGET - contrast) / CONTRAST_TARGET

    region = np.asarray(shaded.convert("RGB"), dtype=np.float32)[area[1] : area[3], area[0] : area[2]]
    hue = float(np.linalg.norm(logo_colour(logo) - np.median(_lab(region.reshape(-1, 3)), axis=0)))
    blend = max(0.0, HUE_TARGET - hue) / HUE_TARGET

    return DETAIL_WEIGHT * detail + FACE_WEIGHT * face + CONTRAST_WEIGHT * shortfall + HUE_WEIGHT * blend
