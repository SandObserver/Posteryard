import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import cast
from urllib.parse import urlsplit

from PIL import Image

from posteryard import http, overrides, pipeline, service_collections
from posteryard.config import Config, EpisodeMode
from posteryard.fanart import Fanart
from posteryard.maintainerr import Maintainerr
from posteryard.notify import Notifier
from posteryard.server import TARGETS, Item, MediaServer, Target, labels
from posteryard.store import Status, Store
from posteryard.tmdb import Tmdb

log = logging.getLogger(__name__)
SUPPORTED = frozenset({"movie", "show", "season", "episode", "collection"})
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
class Restored:
    restored: int = 0
    kept: int = 0
    failed: int = 0


@dataclass
class Leaving:
    days: dict[str, date]
    fetched: float


def jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


class Worker:
    def __init__(
        self, cfg: Config, server: MediaServer, store: Store, notifier: Notifier, tmdb: Tmdb | None = None
    ) -> None:
        self.cfg, self.server, self.store, self.notifier = cfg, server, store, notifier
        self.maintainerr = Maintainerr(cfg.maintainerr_url)
        self.ctx = pipeline.Context(
            tmdb or Tmdb(cfg.tmdb_api_key, cfg.logo_languages),
            cfg.quality,
            cfg.regions,
            {},
            date.today(),
            server,
            labels=cfg.status_labels,
            accessibility=cfg.accessibility,
            episodes=cfg.episodes,
            logo_languages=cfg.logo_languages,
            prefer_wordmark=cfg.prefer_wordmark,
            choices=store,
            overrides=store.override,
            fanart=Fanart(cfg.fanart_api_key) if cfg.fanart_api_key else None,
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
        if item.get("type") == "collection" and not (
            self.cfg.collection_posters
            or (self.cfg.service_collections and service_collections.MANAGED_LABEL in labels(item))
        ):
            return False
        section = item.get("librarySectionTitle")
        if section not in self.cfg.libraries and item.get("type") != "collection":
            return False
        only = self.cfg.only_rating_keys
        related = {str(item.get(k, "")) for k in ("ratingKey", "parentRatingKey", "grandparentRatingKey")}
        return not only or bool(only & related)

    def process(self, rating_key: str, *, force: bool = False) -> Outcome:
        title = rating_key
        try:
            item = self.server.item(rating_key)
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
            if item.get("type") == "episode" and self.cfg.episodes == EpisodeMode.OFF:
                self._restore(item, "thumb")
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
        item = self.server.item(rating_key)
        if item is None:
            raise pipeline.NotFoundError(f"{self.server.name} has no item {rating_key}")
        self._skip_current(item)
        return self.process(rating_key, force=True)

    def reset_art(self, rating_key: str) -> Outcome:
        self.store.reset_override(rating_key)
        return self.process(rating_key, force=True)

    def restore_all(self) -> Restored:
        """Give every uploaded image back to the server's own, also while DRY_RUN is on.

        Images changed by hand are left as they are. A failed item keeps its record, so a second run retries it.
        """
        counts = Restored()
        for record in self.store.with_status(Status.UPLOADED):
            if record.target not in TARGETS:
                continue
            target = cast(Target, record.target)
            try:
                item = self.server.item(record.rating_key)
                if item is not None and self.server.selected(record.rating_key, target) == record.image_key:
                    self.server.restore(item, target)
                    counts.restored += 1
                elif item is not None:
                    counts.kept += 1
            except (http.RequestError, ValueError) as exc:
                log.warning("could not restore the %s of %s: %s", target, record.title, exc)
                counts.failed += 1
                continue
            self.store.forget_target(record.rating_key, record.target)
        return counts

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

    def _restore(self, item: Item, target: Target) -> None:
        """Give back the server's own image when Posteryard no longer makes this kind of image."""
        key = str(item["ratingKey"])
        record = self.store.get(key, target)
        if record is None:
            return
        if record.status == Status.UPLOADED and not self.cfg.dry_run:
            if self.server.selected(key, target) != record.image_key:
                log.info("%s for %s was changed by hand, leaving it", target, item.get("title"))
            else:
                self.server.restore(item, target)
                log.info("gave %s back its own %s", item.get("title"), target)
        self.store.forget_target(key, target)

    def _follow_labels(self, item: Item) -> bool:
        """Apply the labels. True when the poster must be rendered again."""
        key, tags = str(item["ratingKey"]), labels(item)
        redo = False
        if item.get("type") == "collection":
            return redo
        if overrides.NEXT_LABEL in tags:
            self._skip_current(item)
            self.server.remove_label(item, overrides.NEXT_LABEL)
            log.info("switching %s to its next art", item.get("title"))
            redo = True
        current = self.store.override(key)
        if overrides.CUSTOM_LABEL in tags:
            record = self.store.get(key, "poster")
            selected = self.server.selected(key, "poster")
            if selected and (record is None or selected != record.image_key):
                image = overrides.decode(self.server.poster_bytes(item))
                path = str(overrides.save(image, self.cfg.data_dir, key))
                if current is None or current.custom != path:
                    self.store.set_custom(key, path, "plex")
                    log.info(
                        "using the poster uploaded in %s as custom art for %s", self.server.name, item.get("title")
                    )
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
            if record.status == Status.UPLOADED and self.server.selected(key, target) != record.image_key:
                self.store.manual(key, target, plan.name)
                log.info("%s for %s was changed in %s, leaving it alone", target, plan.name, self.server.name)
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
        image_key = self.server.upload(key, target, jpeg(image))
        try:
            self.server.lock(item, target)
        except http.RequestError as exc:
            log.warning("could not lock the %s for %s: %s", target, plan.name, exc)
        self.store.uploaded(key, target, plan.name, plan.fingerprint, image_key)
        log.info("uploaded %s %s (%s)", target, plan.name, "; ".join(plan.notes))
        return Outcome.UPLOADED

    def _failed(self, rating_key: str, title: str, exc: Exception) -> None:
        failures = self.store.failed(rating_key, ITEM_TARGET, title, str(exc))
        log.warning("%s (%s) failed, attempt %d: %s", title, rating_key, failures, exc)
        if failures >= ALERT_AFTER:
            self.notifier.alert(_upstream(exc, self.server), f"{title} failed {failures} times: {exc}")


def _upstream(exc: Exception, server: MediaServer) -> str:
    """Group alerts by what failed, so one outage sends one alert."""
    if isinstance(exc, pipeline.NotFoundError):
        return "missing artwork"
    if isinstance(exc, http.RequestError):
        host = urlsplit(exc.url).hostname or ""
        if host == urlsplit(server.url).hostname:
            return server.name
        if host.endswith("themoviedb.org") or host.endswith("tmdb.org"):
            return "TMDB"
    return "rendering"
