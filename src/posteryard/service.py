import hmac
import itertools
import json
import logging
import queue
import signal
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from posteryard import __version__, http, memory, overrides, service_collections
from posteryard.config import Config
from posteryard.server import Item, MediaServer, is_item_key
from posteryard.store import Store
from posteryard.worker import Outcome, Worker

log = logging.getLogger(__name__)
MAX_BODY = 2 * 1024 * 1024
NEW_EVENTS = frozenset({"library.new"})
JELLYFIN_EVENTS = frozenset({"ItemAdded"})
KINDS = {"movie": ("movie",), "show": ("show", "season", "episode")}
RESTART_LOOKBACK = 6 * 3600
SWEEP_LOOKBACK = 60
WORKER_STALL = 600
TICK = 30
HEARTBEAT_SECONDS = 60
URGENT = frozenset({"webhook", "label", "unignored"})
BACKGROUND = frozenset({"daily", "unlisted"})


def parse_webhook(content_type: str, body: bytes) -> dict[str, Any] | None:
    """Plex posts multipart/form-data with the event JSON in the `payload` field, Emby in the `data` field. Jellyfin's
    Webhook plugin posts the JSON itself, labelled text/plain. Raises ValueError on bad JSON."""
    raw: bytes | None = None
    if not content_type.startswith("multipart/"):
        raw = body
    else:
        message = BytesParser(policy=policy.HTTP).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
        )
        if message.is_multipart():
            for part in message.iter_parts():
                if part.get_param("name", header="content-disposition") in ("payload", "data"):
                    payload = part.get_payload(decode=True)
                    raw = payload if isinstance(payload, bytes) else None
                    break
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except RecursionError:
        raise ValueError("the payload is nested too deeply") from None
    except ValueError:
        if content_type.startswith(("application/json", "multipart/")):
            raise
        return None
    return data if isinstance(data, dict) else None


def other_server(server: MediaServer, store: Store) -> str | None:
    """The data folder records the server it belongs to. Plex and Emby both number items from 1, so records must not
    carry over to another server."""
    current = server.server_id()
    known = store.meta("server")
    if known and known != current:
        return (
            f"The data folder belongs to another media server ({known}), not this {server.name} ({current}). "
            "Use a new data folder, or delete state.db in it to start over."
        )
    if not known:
        store.set_meta("server", current)
    return None


def related_keys(item: object) -> list[str]:
    if not isinstance(item, dict):
        return []
    keys = [item.get("ratingKey"), item.get("parentRatingKey"), item.get("grandparentRatingKey")]
    return [str(k) for k in keys if k]


class Service:
    def __init__(self, cfg: Config, server: MediaServer, store: Store, worker: Worker) -> None:
        self.cfg, self.server, self.store, self.worker = cfg, server, store, worker
        self.queue: queue.PriorityQueue[tuple[int, int, str, str]] = queue.PriorityQueue()
        self._queued: dict[str, int] = {}
        self._order = itertools.count()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.worker_beat = time.monotonic()
        self.last_sweep_ok = time.monotonic()
        self.threads: list[threading.Thread] = []
        self.exit_code = 0
        self.server_checked = threading.Event()
        self._last_heartbeat = float("-inf")

    def enqueue(self, keys: Iterable[str], reason: str) -> None:
        priority = 0 if reason in URGENT else 2 if reason in BACKGROUND else 1
        for key in keys:
            with self._lock:
                if self._queued.get(key, priority + 1) <= priority:
                    continue
                self._queued[key] = priority
                self.queue.put((priority, next(self._order), key, reason))

    def take(self, timeout: float) -> tuple[str, str]:
        while True:
            priority, _, key, reason = self.queue.get(timeout=timeout)
            with self._lock:
                if self._queued.get(key) == priority:
                    del self._queued[key]
                    return key, reason

    def item_added(self, item_id: object) -> None:
        key = str(item_id or "").replace("-", "").lower()
        if not is_item_key(key):
            return
        try:
            item = self.server.item(key)
        except http.RequestError as exc:
            log.warning("webhook item %s could not be read: %s", key, exc)
            item = None
        self.enqueue(related_keys(dict(item)) if item else [key], "webhook")

    def _work(self) -> None:
        while not self._stop.is_set():
            if not self.server_checked.wait(TICK):
                self.worker_beat = time.monotonic()
                continue
            try:
                key, reason = self.take(TICK)
            except queue.Empty:
                self.worker_beat = time.monotonic()
                self.idle()
                self.worker.rest()
                continue
            started = time.monotonic()
            try:
                outcome = self.worker.process(key)
            except Exception:
                log.exception("unexpected error on %s", key)
                outcome = Outcome.FAILED
            if outcome not in (Outcome.UNCHANGED, Outcome.SKIPPED):
                log.info("%s %s (%s) in %.1fs", outcome, key, reason, time.monotonic() - started)
            memory.release()
            self.worker_beat = time.monotonic()

    def _kinds(self, section: Item) -> tuple[str, ...]:
        kinds = KINDS[str(section["type"])]
        if self.cfg.collection_posters or self.cfg.service_collections:
            kinds = (*kinds, "collection")
        return kinds

    def _sections(self) -> list[Item]:
        sections = [
            s for s in self.server.sections() if s.get("title") in self.cfg.libraries and s.get("type") in KINDS
        ]
        if not sections:
            raise LookupError(f"{self.server.name} has no movie or TV library named {', '.join(self.cfg.libraries)}")
        return sections

    def sweep(self, lookback: int = SWEEP_LOOKBACK) -> None:
        since = int(self.store.meta("sweep_cursor", "0")) - lookback
        started = int(time.time())
        for section in self._sections():
            for kind in self._kinds(section):
                changed = self.server.changed_since(str(section["key"]), kind, since)
                self.enqueue((str(i["ratingKey"]) for i in changed), "changed")
                for label in (overrides.CUSTOM_LABEL, overrides.NEXT_LABEL):
                    # Adding a label does not change an item's updatedAt, so changed_since misses it.
                    labelled = self.server.section_items(str(section["key"]), kind, label=label)
                    self.enqueue((str(i["ratingKey"]) for i in labelled), "label")
        ignored = {
            str(i["ratingKey"])
            for section in self._sections()
            for kind in self._kinds(section)
            for i in self.server.section_items(str(section["key"]), kind, label=overrides.IGNORE_LABEL)
        }
        # A removed label leaves updatedAt untouched too, so items that lost the ignore label are found here.
        # Their old records are dropped: a poster chosen while ignored must not count as a manual change.
        released = sorted(set(json.loads(self.store.meta("ignored_keys", "[]"))) - ignored)
        for key in released:
            self.store.forget(key)
        self.enqueue(released, "unignored")
        self.store.set_meta("ignored_keys", json.dumps(sorted(ignored)))
        previous = set(json.loads(self.store.meta("leaving_keys", "[]")))
        current = set(self.worker.leaving_days())
        self.enqueue(sorted(previous | current), "leaving")
        self.enqueue(self.store.retry_due(), "retry")
        self.store.set_meta("leaving_keys", json.dumps(sorted(current)))
        self.store.set_meta("sweep_cursor", str(started))
        self.last_sweep_ok = time.monotonic()

    def idle(self) -> None:
        with self._lock:
            busy = bool(self._queued)
        if not busy and self.store.meta("full_pending") == "1":
            self.store.set_meta("full_pending", "0")
            log.info("full pass finished")

    def resume(self) -> None:
        if self.store.meta("full_pending") == "1":
            log.info("resuming an unfinished full pass")
            self.full()

    def _sync_collections(self) -> None:
        shows = [s for s in self._sections() if s.get("type") == "show"]
        if self.cfg.dry_run:
            log.info("DRY_RUN is on: service collections are not changed")
            return
        if not self.server.collections_per_library and len(shows) > 1:
            log.warning("%s collections belong to no library: service collections use %s only",
                        self.server.name, shows[0].get("title"))  # fmt: skip
            shows = shows[:1]
        for section in shows:
            try:
                service_collections.sync(self.server, self.worker.ctx, str(section["key"]))
            except (http.RequestError, ValueError) as exc:
                log.warning("service collections for %s failed: %s", section.get("title"), exc)
                self.worker.notifier.alert("collections", f"Service collections could not be updated: {exc}")

    def full(self) -> None:
        seen: set[str] = set()
        if self.cfg.service_collections:
            self._sync_collections()
        for section in self._sections():
            for kind in self._kinds(section):
                seen.update(str(i["ratingKey"]) for i in self.server.section_items(str(section["key"]), kind))
        unlisted = sorted(self.store.keys() - seen)
        self.enqueue(sorted(seen), "daily")
        self.enqueue(unlisted, "unlisted")
        self.store.set_meta("full_pending", "1")
        self.store.set_meta("last_full", datetime.now().date().isoformat())
        log.info("full pass queued %d items and %d unlisted ones", len(seen), len(unlisted))

    def settings_signature(self) -> str:
        cfg = self.cfg
        return json.dumps(
            {
                "version": __version__,
                "dry_run": cfg.dry_run,
                "only": sorted(cfg.only_rating_keys),
                "libraries": cfg.libraries,
                "regions": cfg.regions,
                "quality": [cfg.quality.video, cfg.quality.hdr, cfg.quality.audio],
                "status_labels": cfg.status_labels,
                "accessibility": sorted(cfg.accessibility),
                "episodes": cfg.episodes,
                "logo_languages": cfg.logo_languages,
                "prefer_wordmark": cfg.prefer_wordmark,
                "collection_posters": cfg.collection_posters,
                "service_collections": cfg.service_collections,
                "fanart": bool(cfg.fanart_api_key),
                "apple_art": cfg.apple_art,
            },
            sort_keys=True,
        )

    def _schedule(self) -> None:
        next_sweep = 0.0
        lookback = RESTART_LOOKBACK
        resumed = False
        while not self._stop.is_set():
            try:
                if not self.server_checked.is_set():
                    if problem := other_server(self.server, self.store):
                        log.error(problem)
                        self.worker.notifier.alert("server", problem)
                        self.exit_code = 2
                        self._stop.set()
                        return
                    self.server_checked.set()
                signature = self.settings_signature()
                if not resumed:
                    if self.store.meta("settings_signature") == signature:
                        self.resume()
                    resumed = True
                if self.store.meta("settings_signature") != signature:
                    log.info("version or settings changed, checking every item now")
                    self.full()
                    self.store.set_meta("settings_signature", signature)
                now = datetime.now()
                last_full = self.store.meta("last_full")
                if not last_full or (last_full != now.date().isoformat() and now.time() >= self.cfg.daily_at):
                    self.full()
                if time.monotonic() >= next_sweep:
                    self.sweep(lookback)
                    lookback = SWEEP_LOOKBACK
                    next_sweep = time.monotonic() + self.cfg.sweep_minutes * 60
            except (http.RequestError, OSError, ValueError, LookupError) as exc:
                log.warning("scheduled run failed: %s", exc)
                self.worker.notifier.alert("schedule", f"A scheduled run failed: {exc}")
                next_sweep = time.monotonic() + 120
            except Exception as exc:  # The schedule thread must survive any error.
                log.exception("scheduled run failed")
                self.worker.notifier.alert("schedule", f"A scheduled run failed: {type(exc).__name__}: {exc}")
                next_sweep = time.monotonic() + 120
            self.heartbeat()
            self._stop.wait(TICK)

    def heartbeat(self) -> None:
        now = time.monotonic()
        if not self.cfg.heartbeat_url or now - self._last_heartbeat < HEARTBEAT_SECONDS or not self.healthy():
            return
        self._last_heartbeat = now
        try:
            http.request("GET", self.cfg.heartbeat_url, timeout=10, retries=1)
        except http.HttpError as exc:
            log.warning("HEARTBEAT_URL answered HTTP %d", exc.status)
        except http.RequestError:
            log.warning("HEARTBEAT_URL could not be reached")

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        sweep_limit = max(3 * self.cfg.sweep_minutes * 60, 900)
        checks = {
            "threads_running": all(thread.is_alive() for thread in self.threads),
            "worker_responsive": now - self.worker_beat < WORKER_STALL,
            "sweep_recent": now - self.last_sweep_ok < sweep_limit,
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "last_sweep_seconds_ago": round(now - self.last_sweep_ok),
            "last_full_pass": self.store.meta("last_full") or None,
            "full_pass_running": self.store.meta("full_pending") == "1",
            "queue": len(self._queued),
            "images": self.store.counts(),
            "dry_run": self.cfg.dry_run,
            "version": __version__,
        }

    def healthy(self) -> bool:
        return bool(self.status()["ok"])

    def _watch(self, server: ThreadingHTTPServer) -> None:
        while not self._stop.wait(TICK):
            dead = [thread.name for thread in self.threads if not thread.is_alive()]
            if dead:
                log.error("service thread %s stopped, exiting so the container restarts", ", ".join(dead))
                self.worker.notifier.alert("restart", f"Posteryard restarts because {', '.join(dead)} stopped.")
                self.exit_code = 1
                self._stop.set()
        server.shutdown()

    def handler(self) -> type[BaseHTTPRequestHandler]:
        service = self
        webhook_path = f"/webhook/{self.cfg.webhook_secret}"

        class Handler(BaseHTTPRequestHandler):
            timeout = 10

            def log_message(self, format: str, *args: Any) -> None:
                """Silent: the request line holds the webhook secret."""

            def _reply(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path == "/healthz":
                    status = service.status()
                    self._reply(200 if status["ok"] else 503, status)
                else:
                    self._reply(404, {"error": "not found"})

            def do_HEAD(self) -> None:
                if self.path.split("?")[0] != "/healthz":
                    self.send_response(404)
                else:
                    self.send_response(200 if service.healthy() else 503)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                if not hmac.compare_digest(self.path.split("?")[0].encode(), webhook_path.encode()):
                    self._reply(404, {"error": "not found"})
                    return
                raw_length = self.headers.get("Content-Length") or "0"
                length = int(raw_length) if raw_length.isascii() and raw_length.isdigit() else 0
                if not 0 < length <= MAX_BODY:
                    self._reply(413, {"error": "bad size"})
                    return
                try:
                    payload = parse_webhook(self.headers.get("Content-Type", ""), self.rfile.read(length))
                except ValueError:
                    self._reply(400, {"error": "bad payload"})
                    return
                if payload and payload.get("event") in NEW_EVENTS and payload.get("Metadata"):
                    service.enqueue(related_keys(payload["Metadata"]), "webhook")
                elif payload and payload.get("NotificationType") in JELLYFIN_EVENTS:
                    service.item_added(payload.get("ItemId"))
                elif payload and payload.get("Event") in NEW_EVENTS and isinstance(payload.get("Item"), dict):
                    service.item_added(payload["Item"].get("Id"))
                self._reply(200, {"ok": True})

        return Handler

    def run(self) -> int:
        self.threads = [
            threading.Thread(target=self._work, daemon=True, name="worker"),
            threading.Thread(target=self._schedule, daemon=True, name="schedule"),
        ]
        for thread in self.threads:
            thread.start()
        server = ThreadingHTTPServer(("0.0.0.0", self.cfg.listen_port), self.handler())
        server.daemon_threads = True

        def stop(signum: int, _frame: object) -> None:
            log.info("signal %d, shutting down", signum)
            self._stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        log.info(
            "Posteryard %s listening on %d, dry_run=%s, only=%s",
            __version__, self.cfg.listen_port, self.cfg.dry_run, sorted(self.cfg.only_rating_keys) or "all",
        )  # fmt: skip
        threading.Thread(target=self._watch, args=(server,), daemon=True, name="watch").start()
        server.serve_forever()
        server.server_close()
        for thread in self.threads:
            thread.join(timeout=20)
        return self.exit_code
