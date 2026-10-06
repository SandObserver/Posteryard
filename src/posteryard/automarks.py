import json
import threading
from collections import deque
from collections.abc import Callable
from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from posteryard.artwork import ChoiceCache
from posteryard.render import layers
from posteryard.services import Offer, network_key
from posteryard.tmdb import Tmdb

# Part of every automatic mark's key. Change it when the cut changes, so posters using these marks render again.
VERSION = 2
MAX_BORDER_SPREAD = 10.0
MAX_SOFT = 0.15
INK_FROM, INK_RANGE = 40.0, 60.0
WHITE_FROM, WHITE_RANGE = 150.0, 70.0
WHITE_MAX_SPREAD = 40.0
WHITE_COVER = (0.03, 0.45)
LIGHT_FROM, LIGHT_RANGE = 170.0, 50.0
ENCLOSED = 0.55
ICON_RATIO = (0.8, 1.25)
DARK = 0.15
HOLE_CONTRAST = 0.3
MAX_LOGO_DETAIL = 0.12
GREY_SPREAD, GREY_RANGE = 30.0, 50.0
DETAIL_STEP = 40.0
MAX_DETAIL = 0.04
MAX_FILL = 0.85
CHECK_HEIGHT = 100
MAX_THIN = 0.5
SPECK_AREA = 0.002
MAX_SPECKS = 15
NETWORK_SIZE = "w500"


def _alpha_mark(alpha: np.ndarray, *, strict: bool) -> Image.Image | None:
    """None for a filled block, a mark of loose specks, or (strict) one with hairline strokes."""
    mark = Image.new("RGBA", (alpha.shape[1], alpha.shape[0]), (255, 255, 255, 0))
    mark.putalpha(Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8)))
    box = mark.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
    if box is None:
        return None
    ink = mark.getchannel("A").crop(box)
    ink = ink.resize((max(1, round(ink.width * CHECK_HEIGHT / ink.height)), CHECK_HEIGHT))
    solid = np.asarray(ink) > 128
    if not solid.any() or float(solid.mean()) > MAX_FILL:
        return None
    if _specks(solid) > MAX_SPECKS:
        return None
    if strict:
        inner = np.asarray(Image.fromarray(solid.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(3))) > 0
        if float((solid & ~inner).sum() / solid.sum()) > MAX_THIN:
            return None
    return layers.trim(mark)


def _pieces(mask: np.ndarray, ink: np.ndarray, lum: np.ndarray) -> list[tuple[list[tuple[int, int]], float, float]]:
    """Separate pieces of mask, each with the share of its edge on ink and the mean brightness of that ink."""
    height, width = mask.shape
    seen = np.zeros_like(mask)
    pieces: list[tuple[list[tuple[int, int]], float, float]] = []
    for y, x in zip(*np.nonzero(mask), strict=True):
        if seen[y, x]:
            continue
        seen[y, x] = True
        queue, pixels, edge, touching, bright = deque([(int(y), int(x))]), [], 0, 0, 0.0
        while queue:
            cy, cx = queue.pop()
            pixels.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if not (0 <= ny < height and 0 <= nx < width):
                    edge += 1
                elif mask[ny, nx]:
                    if not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
                else:
                    edge += 1
                    if ink[ny, nx]:
                        touching += 1
                        bright += float(lum[ny, nx])
        pieces.append((pixels, touching / edge if edge else 0.0, bright / touching if touching else 0.0))
    return pieces


def _specks(solid: np.ndarray) -> int:
    """Number of separate ink pieces smaller than SPECK_AREA of the image."""
    limit = SPECK_AREA * solid.size
    return sum(len(pixels) < limit for pixels, _, _ in _pieces(solid, solid, solid.astype(np.float32)))


def _detail(rgb: np.ndarray, inside: np.ndarray) -> float:
    """Share of the inside that has colour edges. A one-colour mark loses them."""
    eroded = np.asarray(Image.fromarray((inside * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))) > 0
    if eroded.sum() < 50:
        return 0.0
    gy, gx = np.gradient(rgb, axis=(0, 1))
    step = np.sqrt((gx**2 + gy**2).sum(axis=2))
    return float((step[eroded] > DETAIL_STEP).mean())


def _light(rgb: np.ndarray) -> np.ndarray:
    low = rgb.min(axis=2)
    spread = rgb.max(axis=2) - low
    light: np.ndarray = np.clip((low - LIGHT_FROM) / LIGHT_RANGE, 0, 1) * np.clip(
        1 - (spread - GREY_SPREAD) / GREY_RANGE, 0, 1
    )
    return light


def _grow(mask: np.ndarray) -> np.ndarray:
    grown = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))
    return np.asarray(grown, dtype=np.float32) / 255


def _edge_share(lum: np.ndarray, holes: np.ndarray) -> np.ndarray:
    """How close each pixel's brightness is to the holes', for the soft rim around them."""
    inside = float(lum[holes > 0].mean())
    share: np.ndarray = np.clip(1 - np.abs(lum - inside) / HOLE_CONTRAST, 0, 1)
    return share


def cut(icon: Image.Image) -> Image.Image | None:
    """A one-colour mark from a provider icon: ink on a flat background, else white ink on any background."""
    px = np.asarray(icon.convert("RGB"), dtype=np.float32)
    border = np.concatenate([px[0], px[-1], px[:, 0], px[:, -1]])
    background = np.median(border, axis=0)
    if float(np.abs(border - background).mean()) <= MAX_BORDER_SPREAD:
        distance = np.sqrt(((px - background) ** 2).sum(axis=2))
        alpha = np.clip((distance - INK_FROM) / INK_RANGE, 0, 1)
        covered = int((alpha > 0.05).sum())
        if covered and float(((alpha > 0.1) & (alpha < 0.9)).sum()) / covered <= MAX_SOFT:
            if _detail(px, alpha > 0.95) > MAX_DETAIL:
                return None
            return _alpha_mark(alpha, strict=True)
    return _white_ink(px)


def _white_ink(px: np.ndarray) -> Image.Image | None:
    low = px.min(axis=2)
    spread = px.max(axis=2) - low
    alpha = np.clip((low - WHITE_FROM) / WHITE_RANGE, 0, 1) * np.clip(1 - (spread - WHITE_MAX_SPREAD) / 60, 0, 1)
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    cover = float((alpha > 0.5).mean())
    if float(border.mean()) > 0.05 or not WHITE_COVER[0] < cover < WHITE_COVER[1]:
        return None
    return _alpha_mark(alpha, strict=True)


def one_colour(logo: Image.Image) -> Image.Image | None:
    """A one-colour mark from a transparent logo. Parts set inside ink of another brightness become holes."""
    arr = np.asarray(logo.convert("RGBA"), dtype=np.float32)
    alpha, rgb = arr[..., 3] / 255, arr[..., :3]
    solid = alpha > 0.5
    box = Image.fromarray(solid.astype(np.uint8)).getbbox()
    if box is None:
        return None
    left, top, right, bottom = box
    if (
        solid[top:bottom, left:right].mean() > MAX_FILL
        and ICON_RATIO[0] <= (right - left) / (bottom - top) <= ICON_RATIO[1]
    ):
        return None
    lum = layers.luminance(rgb)
    holes = np.zeros_like(alpha)
    for contrast in (_light(rgb) > 0.5, lum < DARK):
        inside = solid & contrast & (holes == 0)
        rest = int((solid & ~inside).sum())
        for pixels, enclosed, around in _pieces(inside, solid & ~inside & (holes == 0), lum):
            ys, xs = zip(*pixels, strict=True)
            contrasting = abs(around - float(lum[list(ys), list(xs)].mean())) >= HOLE_CONTRAST
            if enclosed >= ENCLOSED and contrasting and len(pixels) < rest:
                holes[list(ys), list(xs)] = 1
    if holes.any():
        rim = _grow(holes) * (holes + (1 - holes) * _edge_share(lum, holes))
        if (mark := _alpha_mark(alpha * (1 - rim), strict=False)) is not None:
            return mark
    if _detail(rgb, alpha > 0.95) > MAX_LOGO_DETAIL:
        return None
    return _alpha_mark(alpha, strict=False)


@cache
def _networks() -> dict[str, list[tuple[int, str]]]:
    raw = json.loads((layers.ASSETS / "networks.json").read_text(encoding="utf-8"))
    return {key: [(int(i), str(c)) for i, c in rows] for key, rows in raw.items()}


def network_for(offer: Offer) -> int | None:
    rows = _networks().get(network_key(offer.name), [])
    for wanted in (offer.region, ""):
        if match := next((i for i, country in rows if country == wanted), None):
            return match
    return rows[0][0] if len(rows) == 1 else None


class AutoMarks:
    def __init__(self, folder: Path, choices: ChoiceCache) -> None:
        self.folder, self.choices = folder, choices
        self._lock = threading.Lock()
        layers.add_mark_dir(folder)

    def get(self, offer: Offer, tmdb: Tmdb | None = None) -> str | None:
        network = network_for(offer) if tmdb is not None else None
        if network is not None and tmdb is not None and (name := self._network(network, tmdb)):
            return name
        if offer.provider_id <= 0 or not offer.logo_path:
            return None
        return self._made(
            f"auto-{offer.provider_id}-v{VERSION}", offer.logo_path, lambda: cut(Tmdb.image(offer.logo_path))
        )

    def _network(self, network: int, tmdb: Tmdb) -> str | None:
        name = f"net-{network}-v{VERSION}"
        hit = self.choices.get_choice(f"automark:{name}")
        if hit is not None and (not hit.get("ok") or (self.folder / f"{name}.png").exists()):
            return name if hit.get("ok") else None
        logo = tmdb.network_logo(network)
        if not logo:
            self.choices.put_choice(f"automark:{name}", {"logo": "", "ok": False})
            return None
        png = logo.removesuffix(".svg").removesuffix(".png") + ".png"
        return self._made(name, logo, lambda: one_colour(Tmdb.image(png, NETWORK_SIZE)))

    def _made(self, name: str, source: str, make: Callable[[], Image.Image | None]) -> str | None:
        path = self.folder / f"{name}.png"
        with self._lock:
            hit = self.choices.get_choice(f"automark:{name}")
            if hit is not None and hit.get("logo") == source and (not hit.get("ok") or path.exists()):
                return name if hit.get("ok") else None
            mark = make()
            if mark is not None:
                self.folder.mkdir(parents=True, exist_ok=True)
                mark.save(path)
            self.choices.put_choice(f"automark:{name}", {"logo": source, "ok": mark is not None})
            return name if mark is not None else None
