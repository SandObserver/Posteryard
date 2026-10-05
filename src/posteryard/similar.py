"""Whether two images show the same picture, also when one is a crop of the other."""

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image, ImageFilter

HEIGHT = 48
SCALES = (1.0, 0.85, 0.7, 0.55, 0.45)
STEP = 2
SPREAD = 8.0
SAME = 0.64
HISTOGRAM_SAME = 0.55
# A second look for dark, busy art: blurred and normalised, it ignores rain or grain drawn differently.
SHAPE_HEIGHT = 40
SHAPE_SCALES = (1.0, 0.85, 0.7, 0.6, 0.5, 0.42, 0.35)
SHAPE_FLOOR = 0.15
SHAPE_SAME = 0.70
SHAPE_COLOUR_SAME = 0.4
SHAPE_HISTOGRAM_SAME = 0.95


@dataclass(frozen=True)
class Thumb:
    colour: np.ndarray
    shape: np.ndarray


def histogram(image: Image.Image) -> list[float]:
    quantised = np.asarray(image.convert("RGB").resize((64, 96)), dtype=np.int32) // 64
    counts = np.bincount((quantised[..., 0] * 16 + quantised[..., 1] * 4 + quantised[..., 2]).ravel(), minlength=64)
    return [round(float(c), 4) for c in counts / counts.sum()]


def thumb(image: Image.Image) -> Thumb:
    rgb = image.convert("RGB")
    colour = rgb.resize((max(8, round(rgb.width * HEIGHT / rgb.height)), HEIGHT), Image.Resampling.BOX)
    small = rgb.resize((max(8, round(rgb.width * SHAPE_HEIGHT / rgb.height)), SHAPE_HEIGHT), Image.Resampling.BOX)
    shape = np.asarray(small.filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    return Thumb(np.asarray(colour, dtype=np.float32), (shape - shape.mean()) / (shape.std() + 1e-3))


def _normalised(patches: np.ndarray, floor: float) -> np.ndarray:
    axes = (-3, -2)
    mean = patches.mean(axis=axes, keepdims=True)
    spread = patches.std(axis=axes, keepdims=True) + floor
    result: np.ndarray = (patches - mean) / spread
    return result


def _resize(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    channels = [Image.fromarray(image[..., c]).resize(size, Image.Resampling.BOX) for c in range(3)]
    return np.stack([np.asarray(c, dtype=np.float32) for c in channels], axis=-1)


def _best(big: np.ndarray, small: np.ndarray, scales: tuple[float, ...], floor: float, step: int) -> float:
    best = -1.0
    for scale in scales:
        h, w = round(small.shape[0] * scale), round(small.shape[1] * scale)
        if w < 8 or w > big.shape[1] or h > big.shape[0]:
            continue
        template = _normalised(_resize(small, (w, h)), floor)
        windows = sliding_window_view(big, (h, w, 3))[::step, ::step, 0]
        best = max(best, float((_normalised(windows, floor) * template).mean(axis=(-3, -2, -1)).max()))
    return best


def _score(a: np.ndarray, b: np.ndarray, scales: tuple[float, ...], floor: float, step: int) -> float:
    return max(_best(a, b, scales, floor, step), _best(b, a, scales, floor, step))


def overlap(a: list[float], b: list[float]) -> float:
    return float(np.minimum(np.asarray(a), np.asarray(b)).sum())


def same_picture(a: Thumb, b: Thumb, histogram_overlap: float, *, redrawn: bool = True) -> bool:
    """The same picture or a crop of it, and with redrawn, also a version with rain or grain drawn differently."""
    colour = _score(a.colour, b.colour, SCALES, SPREAD, STEP)
    if colour >= SAME:
        return True
    if not redrawn or colour < SHAPE_COLOUR_SAME or histogram_overlap < SHAPE_HISTOGRAM_SAME:
        return False
    return _score(a.shape, b.shape, SHAPE_SCALES, SHAPE_FLOOR, 1) >= SHAPE_SAME
