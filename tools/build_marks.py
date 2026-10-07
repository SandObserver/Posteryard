"""Rasterise assets/marks-src/*.svg into white marks in src/posteryard/assets/marks.

A .png source has no vector version; its white ink becomes the mark.

Run after adding or replacing a mark: uv run python tools/build_marks.py
"""

import io
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "marks-src"
OUT = ROOT / "src" / "posteryard" / "assets" / "marks"
HEIGHT = 240
KNOCKOUT = {"youtube"}
WHITE_FROM, WHITE_RANGE = 170.0, 50.0
EDGE = (100, 160)


def whiten(svg: Path) -> Image.Image:
    png = resvg_py.svg_to_bytes(svg_path=str(svg), height=HEIGHT * 2)
    img = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    alpha = img.getchannel("A")
    if svg.stem in KNOCKOUT:
        hole = img.convert("L").point(lambda v: 255 if v > 200 else 0)
        alpha = Image.composite(Image.new("L", img.size, 0), alpha, hole)
    out = Image.new("RGBA", img.size, (255, 255, 255, 255))
    out.putalpha(alpha)
    box = out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    out = out.crop(box) if box else out
    return out.resize((round(out.width * HEIGHT / out.height), HEIGHT), Image.Resampling.LANCZOS)


def white_ink(png: Path) -> Image.Image:
    px = np.asarray(Image.open(png).convert("RGBA"), dtype=np.float32)
    ink = np.clip((px[..., :3].min(axis=2) - WHITE_FROM) / WHITE_RANGE, 0, 1) * px[..., 3] / 255
    alpha = Image.fromarray((ink * 255).astype(np.uint8))
    box = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    alpha = alpha.crop(box) if box else alpha
    alpha = alpha.resize((round(alpha.width * HEIGHT * 2 / alpha.height), HEIGHT * 2), Image.Resampling.LANCZOS)
    low, high = EDGE
    alpha = alpha.filter(ImageFilter.GaussianBlur(2)).point(
        lambda v: 0 if v < low else 255 if v > high else (v - low) * 255 // (high - low)
    )
    out = Image.new("RGBA", alpha.size, (255, 255, 255, 255))
    out.putalpha(alpha)
    return out.resize((round(out.width * HEIGHT / out.height), HEIGHT), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for source in sorted([*SRC.glob("*.svg"), *SRC.glob("*.png")]):
        mark = whiten(source) if source.suffix == ".svg" else white_ink(source)
        mark.save(OUT / f"{source.stem}.png", optimize=True)
        print("built", source.stem)


if __name__ == "__main__":
    main()
