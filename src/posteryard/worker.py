"""Process one Plex item: plan, skip what is unchanged, render, then preview or upload."""

import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.parse import urlsplit

from PIL import Image

from posteryard import http, overrides, pipeline
from posteryard.config import Config
from posteryard.maintainerr import Maintainerr
from posteryard.notify import Notifier
from posteryard.plex import Item, Plex, labels
from posteryard.store import Status, Store
from posteryard.tmdb import Tmdb

log = logging.getLogger(__name__)
SUPPORTED = frozenset({"movie", "show", "season", "episode"})
ALERT_AFTER = 3
LEAVING_CACHE_SECONDS = 600
ITEM_TARGET = "item"
JPEG_QUALITY = 90


class Outcome(StrEnum):
    GONE = "gone"
    SKIPPED = "skipped"
    UNCHANGED = "unchanged"
    MANUAL = "manual"
    PREVIEW = "preview"
    UPLOADED = "uploaded"
    FAILED = "failed"


@dataclass
class Leaving:
    days: dict[str, date]
    fetched: float


def jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


class Worker:
    def __init__(self, cfg: Config, plex: Plex, store: Store, notifier: Notifier, tmdb: Tmdb | None = None) -> None:
        self.cfg, self.plex, self.store, self.notifier = cfg, plex, store, notifier
        self.maintainerr = Maintainerr(cfg.maintainerr_url)
        self.ctx = pipeline.Context(
            tmdb or Tmdb(cfg.tmdb_api_key),
            cfg.quality,
            cfg.regions,
            {},
            date.today(),
            plex,
            choices=store,
            overrides=store.override,
        )
        self._leaving: Leaving | None = None

    def leaving_days(self) -> dict[str, date]:
        """Maintainerr's schedule. While Maintainerr is down, the last known one."""
        if self._leaving and time.monotonic() - self._leaving.fetched < LEAVING_CACHE_SECONDS:
            return self._leaving.days
        try:
            days = self.maintainerr.action_days()
            self.store.set_meta("leaving_days", json.dumps({k: v.isoformat() for k, v in days.items()}))
        except (http.RequestError, ValueError) as exc:
            log.warning("Maintainerr unavailable, using its last known schedule: %s", exc)
            self.notifier.alert("Maintainerr", f"Maintainerr is unavailable: {exc}")
            saved = json.loads(self.store.meta("leaving_days", "{}"))
            days = {k: date.fromisoformat(v) for k, v in saved.items()}
        self._leaving = Leaving(days, time.monotonic())
        return days

    def allowed(self, item: Item) -> bool:
        if item.get("type") not in SUPPORTED:
            return False
        section = item.get("librarySectionTitle")
        if section and section not in self.cfg.libraries:
            return False
        only = self.cfg.only_rating_keys
        related = {str(item.get(k, "")) for k in ("ratingKey", "parentRatingKey", "grandparentRatingKey")}
        return not only or bool(only & related)

    def process(self, rating_key: str, *, force: bool = False) -> Outcome:
        title = rating_key
        try:
            item = self.plex.item(rating_key)
            if item is None:
                self.store.forget(rating_key)
                self.store.reset_override(rating_key)
                return Outcome.GONE
            if not self.allowed(item) or overrides.IGNORE_LABEL in labels(item):
                return Outcome.SKIPPED
            title = str(item.get("title", rating_key))
            self.ctx.action_days = self.leaving_days()
            self.ctx.today = date.today()
            redo_poster = self._follow_labels(item)
            outcomes = [
                self._apply(plan, item, force or (redo_poster and plan.target == "poster"))
                for plan in pipeline.plan_item(self.ctx, item)
            ]
        except (http.RequestError, pipeline.NotFoundError, overrides.ArtError, OSError, ValueError) as exc:
            self._failed(rating_key, title, exc)
            return Outcome.FAILED
        self.store.forget_target(rating_key, ITEM_TARGET)
        for outcome in (Outcome.UPLOADED, Outcome.PREVIEW, Outcome.MANUAL, Outcome.UNCHANGED):
            if outcome in outcomes:
                return outcome
        return Outcome.SKIPPED

    def set_custom(self, rating_key: str, image: Image.Image) -> Outcome:
        path = overrides.save(image, self.cfg.data_dir, rating_key)
        self.store.set_custom(rating_key, str(path), "command")
        return self.process(rating_key, force=True)

    def next_art(self, rating_key: str) -> Outcome:
        item = self.plex.item(rating_key)
        if item is None:
            raise pipeline.NotFoundError(f"Plex has no item {rating_key}")
        self._skip_current(item)
        return self.process(rating_key, force=True)

    def reset_art(self, rating_key: str) -> Outcome:
        self.store.reset_override(rating_key)
        return self.process(rating_key, force=True)

    def _skip_current(self, item: Item) -> None:
        key = str(item["ratingKey"])
        poster = next((p for p in pipeline.plan_item(self.ctx, item) if p.target == "poster"), None)
        if poster is None:
            raise pipeline.NotFoundError(f"{item.get('title')} has no poster to replace")
        art = str(poster.inputs["art"])
        if art.startswith(overrides.FILE_PREFIX):
            self.store.reset_override(key)
        else:
            self.store.add_skip(key, art)

    def _follow_labels(self, item: Item) -> bool:
        """Apply the Plex labels. True when the poster must be rendered again."""
        key, tags = str(item["ratingKey"]), labels(item)
        redo = False
        if overrides.NEXT_LABEL in tags:
            self._skip_current(item)
            self.plex.remove_label(item, overrides.NEXT_LABEL)
            log.info("switching %s to its next art", item.get("title"))
            redo = True
        current = self.store.override(key)
        if overrides.CUSTOM_LABEL in tags:
            record = self.store.get(key, "poster")
            selected = self.plex.selected(key, "poster")
            if selected and (record is None or selected != record.image_key):
                image = overrides.decode(self.plex.poster_bytes(item))
                path = str(overrides.save(image, self.cfg.data_dir, key))
                if current is None or current.custom != path:
                    self.store.set_custom(key, path, "plex")
                    log.info("using the poster uploaded in Plex as custom art for %s", item.get("title"))
                    redo = True
        elif current is not None and current.source == "plex":
            self.store.reset_override(key)
            log.info("custom art label removed from %s, back to automatic art", item.get("title"))
            redo = True
        return redo

    def _apply(self, plan: pipeline.Plan, item: Item, force: bool) -> Outcome:
        key, target = plan.rating_key, plan.target
        record = self.store.get(key, target)
        if not self.cfg.dry_run and record and not force:
            if record.status == Status.MANUAL:
                return Outcome.MANUAL
            if record.status == Status.UPLOADED and self.plex.selected(key, target) != record.image_key:
                self.store.manual(key, target, plan.name)
                log.info("%s for %s was changed in Plex, leaving it alone", target, plan.name)
                return Outcome.MANUAL
        wanted = Status.PREVIEW if self.cfg.dry_run else Status.UPLOADED
        if record and record.status == wanted and record.fingerprint == plan.fingerprint and not force:
            return Outcome.UNCHANGED
        image = plan.draw()
        if self.cfg.dry_run:
            self.cfg.preview_dir.mkdir(parents=True, exist_ok=True)
            image.convert("RGB").save(self.cfg.preview_dir / f"{key}-{target}.jpg", quality=JPEG_QUALITY)
            self.store.previewed(key, target, plan.name, plan.fingerprint)
            log.info("previewed %s %s (%s)", target, plan.name, "; ".join(plan.notes))
            return Outcome.PREVIEW
        image_key = self.plex.upload(key, target, jpeg(image))
        try:
            self.plex.lock(item, target)
        except http.RequestError as exc:
            log.warning("could not lock the %s for %s: %s", target, plan.name, exc)
        self.store.uploaded(key, target, plan.name, plan.fingerprint, image_key)
        log.info("uploaded %s %s (%s)", target, plan.name, "; ".join(plan.notes))
        return Outcome.UPLOADED

    def _failed(self, rating_key: str, title: str, exc: Exception) -> None:
        failures = self.store.failed(rating_key, ITEM_TARGET, title, str(exc))
        log.warning("%s (%s) failed, attempt %d: %s", title, rating_key, failures, exc)
        if failures >= ALERT_AFTER:
            self.notifier.alert(_upstream(exc, self.cfg.plex_url), f"{title} failed {failures} times: {exc}")


def _upstream(exc: Exception, plex_url: str) -> str:
    """Group alerts by what failed, so one outage sends one alert."""
    if isinstance(exc, pipeline.NotFoundError):
        return "missing artwork"
    if isinstance(exc, http.RequestError):
        host = urlsplit(exc.url).hostname or ""
        if host == urlsplit(plex_url).hostname:
            return "Plex"
        if host.endswith("themoviedb.org") or host.endswith("tmdb.org"):
            return "TMDB"
    return "rendering"
