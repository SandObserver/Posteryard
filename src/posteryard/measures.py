import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from posteryard import http, similar
from posteryard.artwork import remember
from posteryard.render import designs, lines
from posteryard.render.layers import cover, trim
from posteryard.sources import Sources

SAME_PICTURE_BITS = 10
THUMB_CACHE = 2000
FEATURE_CACHE = 64


@dataclass
class Measures:
    sources: Sources
    prefix: str
    thumbs: OrderedDict[str, similar.Thumb] = field(default_factory=OrderedDict)
    features: OrderedDict[str, similar.Features] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def fade(  # noqa: PLR0913
        self,
        art: str,
        logo: str | None,
        name: str,
        below: list[lines.Line],
        *,
        label: lines.Label | None,
        prefer: str = "",
    ) -> float:
        text = name + (f"@{prefer}" if prefer else "")
        key = f"fade:{art}:{logo or text}:{below!r}:{label.text if label else ''}"

        def measure() -> Mapping[str, Any]:
            image = trim(self.sources.fetch(logo)) if logo else designs.text_logo(name, prefer=prefer)
            return {"strength": designs.fade_strength(self.sources.load(art), image, below, label)}

        return float(self._measured(key, measure)["strength"])

    def corner_dark(self, path: str, area: str) -> bool:
        def measure() -> Mapping[str, Any]:
            return {"dark": designs.corner_dark(self.sources.load(path), area)}

        return bool(self._measured(f"corner:{path}:{area}", measure)["dark"])

    def dark_reads(  # noqa: PLR0913
        self,
        art: str,
        logo: str | None,
        name: str,
        below: list[lines.Line],
        *,
        label: lines.Label | None,
        prefer: str = "",
    ) -> bool:
        key = f"dark-ink:{art}:{logo or name}:{prefer}:{below!r}:{label.text if label else ''}"

        def measure() -> Mapping[str, Any]:
            image = trim(self.sources.fetch(logo)) if logo else designs.text_logo(name, prefer=prefer)
            return {"reads": designs.dark_logo_reads(self.sources.load(art), image, below, label)}

        return bool(self._measured(key, measure)["reads"])

    def one_colour(self, logo: str) -> bool:
        def measure() -> Mapping[str, Any]:
            return {"one": designs.one_colour(trim(self.sources.fetch(logo)))}

        return bool(self._measured(f"logo-colour:{logo}", measure)["one"])

    def _measured(self, key: str, measure: Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
        return remember(self.sources.choices, self.prefix + key, measure)

    def same_picture(self, a: str, b: str, *, redrawn: bool = True) -> bool:
        """An image the server no longer has is a different picture. Art skipped long ago can be deleted."""
        try:
            first, second = self._picture(a), self._picture(b)
            if bin(int(first["hash"]) ^ int(second["hash"])).count("1") <= SAME_PICTURE_BITS:
                return True
            shared = similar.overlap(first["histogram"], second["histogram"])
            if shared >= similar.HISTOGRAM_SAME and similar.same_picture(
                self._thumb(a), self._thumb(b), shared, redrawn=redrawn
            ):
                return True
            return redrawn and self._same_details(a, b)
        except http.HttpError as exc:
            if 400 <= exc.status < 500 and exc.status not in http.RETRY_STATUSES:
                return False
            raise

    def _picture(self, path: str) -> Mapping[str, Any]:
        choices = self.sources.choices
        hit = choices.get_choice(f"poster-hash:{path}")
        if hit is not None and "histogram" in hit:
            return hit
        image = self.sources.fetch(path)
        shown = cover(image, 90, 135)
        grey = np.asarray(shown.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
        bits = (grey[:, 1:] > grey[:, :-1]).flatten()
        value = int("".join("1" if b else "0" for b in bits), 2)
        hit = {"hash": value, "histogram": similar.histogram(image)}
        choices.put_choice(f"poster-hash:{path}", hit)
        return hit

    def _same_details(self, a: str, b: str) -> bool:
        first, second = sorted((a, b))

        def measure() -> Mapping[str, Any]:
            return {"same": similar.same_details(self._features(first), self._features(second))}

        key = f"same-details:{similar.FEATURE_RULE}:{first}:{second}"
        return bool(remember(self.sources.choices, key, measure)["same"])

    def _features(self, path: str) -> similar.Features:
        with self._lock:
            hit = self.features.get(path)
            if hit is not None:
                self.features.move_to_end(path)
                return hit
        value = similar.features(self.sources.fetch(path))
        with self._lock:
            self.features[path] = value
            while len(self.features) > FEATURE_CACHE:
                self.features.popitem(last=False)
        return value

    def _thumb(self, path: str) -> similar.Thumb:
        with self._lock:
            hit = self.thumbs.get(path)
            if hit is not None:
                self.thumbs.move_to_end(path)
                return hit
        value = similar.thumb(self.sources.fetch(path))
        with self._lock:
            self.thumbs[path] = value
            while len(self.thumbs) > THUMB_CACHE:
                self.thumbs.popitem(last=False)
        return value
