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
MAX_BORDER_SPREAD = 10.0
MAX_SOFT = 0.15
INK_FROM, INK_RANGE = 40.0, 60.0


def cut(icon: Image.Image) -> Image.Image | None:
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
