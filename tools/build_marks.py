"""Rasterise assets/marks-src/*.svg into white marks in src/posteryard/assets/marks.

Run after adding or replacing a mark: uv run python tools/build_marks.py
"""

import io
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "marks-src"
OUT = ROOT / "src" / "posteryard" / "assets" / "marks"
HEIGHT = 240
KNOCKOUT = {"youtube"}


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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for svg in sorted(SRC.glob("*.svg")):
        whiten(svg).save(OUT / f"{svg.stem}.png", optimize=True)
        print("built", svg.stem)


if __name__ == "__main__":
    main()
