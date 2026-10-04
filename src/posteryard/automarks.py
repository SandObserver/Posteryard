"""Marks for streaming services without a built-in one, cut from TMDB's provider icon when the cut is clean."""

import threading
from pathlib import Path

import numpy as np
from PIL import Image

from posteryard.artwork import ChoiceCache
from posteryard.render import layers
from posteryard.services import Offer
from posteryard.tmdb import Tmdb

# Part of every automatic mark's key. Change it when the cut changes, so posters using these marks render again.
VERSION = 1
# Icons whose border varies more than this have no flat background to cut away.
MAX_BORDER_SPREAD = 10.0
# Gradients and shadows leave many half-transparent pixels; a clean logo leaves few.
MAX_SOFT = 0.15
INK_FROM, INK_RANGE = 40.0, 60.0


def cut(icon: Image.Image) -> Image.Image | None:
    """The icon's logo as a white mark on transparency, or None when the icon has no clean flat background."""
    px = np.asarray(icon.convert("RGB"), dtype=np.float32)
    border = np.concatenate([px[0], px[-1], px[:, 0], px[:, -1]])
    background = np.median(border, axis=0)
    if float(np.abs(border - background).mean()) > MAX_BORDER_SPREAD:
        return None
    distance = np.sqrt(((px - background) ** 2).sum(axis=2))
    alpha = np.clip((distance - INK_FROM) / INK_RANGE, 0, 1)
    covered = int((alpha > 0.05).sum())
    if covered == 0 or float(((alpha > 0.1) & (alpha < 0.9)).sum()) / covered > MAX_SOFT:
        return None
    mark = Image.new("RGBA", icon.size, (255, 255, 255, 0))
    mark.putalpha(Image.fromarray((alpha * 255).astype(np.uint8)))
    return layers.trim(mark)


class AutoMarks:
    def __init__(self, folder: Path, choices: ChoiceCache) -> None:
        self.folder, self.choices = folder, choices
        self._lock = threading.Lock()
        layers.add_mark_dir(folder)

    def get(self, offer: Offer) -> str | None:
        """The mark's name for `layers.mark`, or None when TMDB's icon does not give a clean mark."""
        if offer.provider_id <= 0 or not offer.logo_path:
            return None
        name = f"auto-{offer.provider_id}-v{VERSION}"
        path = self.folder / f"{name}.png"
        with self._lock:
            hit = self.choices.get_choice(f"automark:{name}")
            if hit is not None and hit.get("logo") == offer.logo_path and (not hit.get("ok") or path.exists()):
                return name if hit.get("ok") else None
            mark = cut(Tmdb.image(offer.logo_path))
            if mark is not None:
                self.folder.mkdir(parents=True, exist_ok=True)
                mark.save(path)
            self.choices.put_choice(f"automark:{name}", {"logo": offer.logo_path, "ok": mark is not None})
            return name if mark is not None else None
