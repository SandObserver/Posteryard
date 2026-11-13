import atexit
import multiprocessing
import os
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
from posteryard.render.layers import flatten
from posteryard.titles import plain

# onnxruntime reads this once, at import. Without it, onnxruntime contacts Microsoft's telemetry server.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

READ_WIDTH = 480
# Mid grey, so both dark and light text on a transparent image can be read.
TRANSPARENT_READS_AS = (128, 128, 128)
# Each onnxruntime thread holds its own buffers. More threads push the service past its memory limit.
OCR_THREADS = 2
READS_PER_PROCESS = 100
READ_TIMEOUT = 120
IDLE_SECONDS = 600
STOP_SECONDS = 5
MIN_SCORE = 0.6
MIN_MATCH_LENGTH = 4
MIN_KEY_LENGTH = 3
# A shorter Latin title matches inside too many words. It counts only as whole words.
MIN_ANYWHERE_LENGTH = 6
# OCR reads large shapes as one character, so a one-character title is not checked.
MIN_SHORT_KEY_LENGTH = 2
DISPLAY_HEIGHT = 0.035
DISPLAY_WIDTH = 0.35
# OCR reads large shapes such as a window or an emblem as one character. One character is not display text.
MIN_DISPLAY_LENGTH = 2


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
        future = self.pool.submit(_read_pixels, pixels)
        try:
            return future.result(timeout=READ_TIMEOUT)
        except TimeoutError:
            self.close()
            raise

    def close(self, idle: float = 0) -> None:
        """Do not wait for a running read. A stuck read would block the worker thread for good."""
        if self.pool is None or time.monotonic() - self.last_read < idle:
            return
        pool, self.pool = self.pool, None
        processes = list((pool._processes or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            process.join(STOP_SECONDS)
            if process.is_alive():
                process.kill()
                process.join()


_process = _Process()
atexit.register(_process.close)


def close_idle() -> None:
    _process.close(IDLE_SECONDS)


def read(image: Image.Image) -> list[TextLine]:
    rgb = flatten(image, TRANSPARENT_READS_AS)
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


def _words(text: str) -> list[str]:
    return [word for word in map(plain, text.split()) if word]


def _contains(key: str, text: str, words: Sequence[str]) -> bool:
    if len(key) >= MIN_ANYWHERE_LENGTH or not key.isascii():
        return key in text
    return any(
        "".join(words[start:end]) == key for start in range(len(words)) for end in range(start + 1, len(words) + 1)
    )


def _part_of(text: str, key: str) -> bool:
    """One word from the middle of a long title is not that title."""
    return text in key and (len(text) * 2 >= len(key) or key.startswith(text) or key.endswith(text))


def shows_title(lines: Iterable[TextLine], titles: Sequence[str]) -> bool:
    confident = [line for line in lines if line.score >= MIN_SCORE]
    names = {plain(t) for t in titles}
    keys = [k for k in names if len(k) >= MIN_KEY_LENGTH]
    # A short title matches inside too many words. It counts only as a whole line.
    short = {k for k in names if MIN_SHORT_KEY_LENGTH <= len(k) < MIN_KEY_LENGTH}
    joined = plain(" ".join(line.text for line in confident))
    for line in confident:
        text = plain(line.text)
        if text in short:
            return True
        if len(text) >= MIN_MATCH_LENGTH and any(
            _part_of(text, key) or _contains(key, text, _words(line.text)) for key in keys
        ):
            return True
    words = [word for line in confident for word in _words(line.text)]
    return any(_contains(key, joined, words) for key in keys)


def is_display(line: TextLine) -> bool:
    return (
        line.score >= MIN_SCORE
        and line.height > DISPLAY_HEIGHT
        and line.width > DISPLAY_WIDTH
        and len(plain(line.text)) >= MIN_DISPLAY_LENGTH
    )


def has_display_text(lines: Iterable[TextLine]) -> bool:
    return any(is_display(line) for line in lines)


def confident_text(lines: Iterable[TextLine]) -> str:
    return " ".join(line.text for line in lines if line.score >= MIN_SCORE)
