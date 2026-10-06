import atexit
import multiprocessing
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np
from PIL import Image

from posteryard import memory

READ_WIDTH = 480
# Each onnxruntime thread holds its own buffers. More threads push the service past its memory limit.
OCR_THREADS = 2
READS_PER_PROCESS = 100
READ_TIMEOUT = 120
IDLE_SECONDS = 600
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
    import onnxruntime  # type: ignore[import-untyped]  # noqa: PLC0415
    from rapidocr import RapidOCR  # noqa: PLC0415

    onnxruntime.set_default_logger_severity(3)
    return RapidOCR(
        params={
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )


class _Process:
    """One OCR process, so the onnxruntime engine never lives in the service process."""

    def __init__(self) -> None:
        self.pool: ProcessPoolExecutor | None = None
        self.last_read = float("-inf")

    def read(self, pixels: np.ndarray) -> list[TextLine]:
        self.last_read = time.monotonic()
        if self.pool is None:
            self.pool = ProcessPoolExecutor(
                max_workers=1,
                max_tasks_per_child=READS_PER_PROCESS,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return self.pool.submit(_read_pixels, pixels).result(timeout=READ_TIMEOUT)

    def close(self, idle: float = 0) -> None:
        if self.pool is not None and time.monotonic() - self.last_read >= idle:
            self.pool.shutdown(cancel_futures=True)
            self.pool = None


_process = _Process()
atexit.register(_process.close)


def close_idle() -> None:
    _process.close(IDLE_SECONDS)


def read(image: Image.Image) -> list[TextLine]:
    rgb = image.convert("RGB")
    rgb = rgb.resize((READ_WIDTH, max(1, round(rgb.height * READ_WIDTH / rgb.width))))
    pixels = np.asarray(rgb)
    for _ in range(2):
        try:
            return _process.read(pixels)
        except BrokenProcessPool:
            _process.close()
    raise OSError("the OCR process stopped twice in a row")


def _read_pixels(pixels: np.ndarray) -> list[TextLine]:
    height, width = pixels.shape[:2]
    result = _engine()(pixels)
    lines = []
    if result.txts is not None:
        for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
            xs, ys = box[:, 0], box[:, 1]
            lines.append(
                TextLine(
                    text=str(text),
                    score=float(score),
                    height=float(ys.max() - ys.min()) / height,
                    width=float(xs.max() - xs.min()) / width,
                )
            )
    memory.release()
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


def is_display(line: TextLine) -> bool:
    return line.score >= MIN_SCORE and line.height > DISPLAY_HEIGHT and line.width > DISPLAY_WIDTH


def has_display_text(lines: Iterable[TextLine]) -> bool:
    return any(is_display(line) for line in lines)


def confident_text(lines: Iterable[TextLine]) -> str:
    return " ".join(line.text for line in lines if line.score >= MIN_SCORE)
