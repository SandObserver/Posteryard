"""Fetch fallback fonts for scripts Inter lacks into src/posteryard/assets/fonts.

Run after changing a source or commit: uv run python tools/build_fonts.py
"""

import io
import urllib.parse
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from posteryard import http

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "posteryard" / "assets" / "fonts"
GOOGLE = "https://raw.githubusercontent.com/google/fonts/7085eb89a950e85db5b166b7a58d414544b4140c/ofl"
VAZIRMATN = "https://raw.githubusercontent.com/rastikerdar/vazirmatn/6e553e33489a8f9dfaccc76860a2e3f3c1e66de7"
PRETENDARD = (
    "https://raw.githubusercontent.com/orioncactus/pretendard/7aeb0698819be2b4097dae8ec8fe6a795e5cf3ae/packages"
)
WEIGHTS = {"SemiBold": 600.0, "Bold": 700.0}
STATIC: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "Vazirmatn": (f"{VAZIRMATN}/fonts/ttf/Vazirmatn-{{weight}}.ttf", f"{VAZIRMATN}/OFL.txt", ("SemiBold", "Bold")),
    "Pretendard": (
        f"{PRETENDARD}/pretendard/dist/public/static/Pretendard-{{weight}}.otf",
        f"{PRETENDARD}/pretendard/dist/LICENSE.txt",
        ("SemiBold", "Bold"),
    ),
    "PretendardJP": (
        f"{PRETENDARD}/pretendard-jp/dist/public/static/PretendardJP-{{weight}}.otf",
        f"{PRETENDARD}/pretendard-jp/dist/LICENSE.txt",
        ("SemiBold", "Bold"),
    ),
}
VARIABLE: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "NotoSansHebrew": ("notosanshebrew", "NotoSansHebrew[wdth,wght].ttf", ("SemiBold", "Bold")),
    "NotoSansThai": ("notosansthai", "NotoSansThai[wdth,wght].ttf", ("SemiBold", "Bold")),
    "NotoSansDevanagari": ("notosansdevanagari", "NotoSansDevanagari[wdth,wght].ttf", ("SemiBold", "Bold")),
    "NotoSansSC": ("notosanssc", "NotoSansSC[wght].ttf", ("Bold",)),
    "NotoSansTC": ("notosanstc", "NotoSansTC[wght].ttf", ("Bold",)),
}


def fetch(url: str) -> bytes:
    return http.request("GET", url, timeout=300)


def report(path: Path) -> None:
    print(f"built {path.name}: {path.stat().st_size / 1_048_576:.1f} MB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for family, (pattern, licence, weights) in STATIC.items():
        for weight in weights:
            path = OUT / f"{family}-{weight}{Path(pattern).suffix}"
            path.write_bytes(fetch(pattern.format(weight=weight)))
            report(path)
        (OUT / f"OFL-{family}.txt").write_bytes(fetch(licence))
    for family, (folder, name, weights) in VARIABLE.items():
        source = fetch(f"{GOOGLE}/{folder}/{urllib.parse.quote(name)}")
        for weight in weights:
            font = TTFont(io.BytesIO(source))
            axes = {axis.axisTag for axis in font["fvar"].axes}
            limits = {"wght": WEIGHTS[weight], **({"wdth": 100.0} if "wdth" in axes else {})}
            path = OUT / f"{family}-{weight}.ttf"
            instancer.instantiateVariableFont(font, limits).save(path)
            report(path)
        (OUT / f"OFL-{family}.txt").write_bytes(fetch(f"{GOOGLE}/{folder}/OFL.txt"))


if __name__ == "__main__":
    main()
