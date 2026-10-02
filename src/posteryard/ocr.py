"""Read the text printed on artwork."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np
from PIL import Image

READ_WIDTH = 480
MIN_SCORE = 0.6
MIN_MATCH_LENGTH = 4
DISPLAY_HEIGHT = 0.035
DISPLAY_WIDTH = 0.35


@dataclass(frozen=True)
class TextLine:
    text: str
    score: float
    height: float
    width: float


@cache
def _engine() -> Any:
    from rapidocr import RapidOCR  # noqa: PLC0415

    return RapidOCR(params={"Global.log_level": "error"})


def read(image: Image.Image) -> list[TextLine]:
    rgb = image.convert("RGB")
    rgb = rgb.resize((READ_WIDTH, max(1, round(rgb.height * READ_WIDTH / rgb.width))))
    result = _engine()(np.asarray(rgb))
    if result.txts is None:
        return []
    lines = []
    for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
        xs, ys = box[:, 0], box[:, 1]
        lines.append(
            TextLine(
                text=str(text),
                score=float(score),
                height=float(ys.max() - ys.min()) / rgb.height,
                width=float(xs.max() - xs.min()) / rgb.width,
            )
        )
    return lines


def normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def shows_title(lines: Iterable[TextLine], titles: Sequence[str]) -> bool:
    keys = [k for k in (normalise(t) for t in titles) if len(k) >= 3]
    joined = normalise(" ".join(line.text for line in lines if line.score >= MIN_SCORE))
    for line in lines:
        text = normalise(line.text)
        if line.score < MIN_SCORE or len(text) < MIN_MATCH_LENGTH:
            continue
        if any(text in key or key in text for key in keys):
            return True
    return any(key in joined for key in keys)


def has_display_text(lines: Iterable[TextLine]) -> bool:
    return any(
        line.score >= MIN_SCORE and line.height > DISPLAY_HEIGHT and line.width > DISPLAY_WIDTH for line in lines
    )
