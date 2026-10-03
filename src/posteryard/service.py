"""Webhook receiver, work queue and schedule."""

import hmac
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

from posteryard import __version__, http, memory, overrides
from posteryard.config import Config
from posteryard.plex import Item, Plex
from posteryard.store import Store
from posteryard.worker import Outcome, Worker

log = logging.getLogger(__name__)
MAX_BODY = 2 * 1024 * 1024
NEW_EVENTS = frozenset({"library.new"})
KINDS = {"movie": ("movie",), "show": ("show", "season", "episode")}
RESTART_LOOKBACK = 6 * 3600
SWEEP_LOOKBACK = 60
WORKER_STALL = 600
TICK = 30


def parse_webhook(content_type: str, body: bytes) -> dict[str, Any] | None:
    """Plex posts multipart/form-data with the event JSON in the `payload` field."""
    if content_type.startswith("application/json"):
        data: dict[str, Any] = json.loads(body)
        return data
    message = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    if not message.is_multipart():
        return None
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "payload":
            payload = part.get_payload(decode=True)
            return json.loads(payload) if isinstance(payload, bytes) else None
    return None


def related_keys(item: Item) -> list[str]:
    """A new episode can also need its season and show artwork."""
    keys = [item.get("ratingKey"), item.get("parentRatingKey"), item.get("grandparentRatingKey")]
    return [str(k) for k in keys if k]


class Service:
    def __init__(self, cfg: Config, plex: Plex, store: Store, worker: Worker) -> None:
        self.cfg, self.plex, self.store, self.worker = cfg, plex, store, worker
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.worker_beat = time.monotonic()
        self.last_sweep_ok = time.monotonic()
        self.threads: list[threading.Thread] = []

    def enqueue(self, keys: Iterable[str], reason: str) -> None:
        for key in keys:
            with self._lock:
                if key in self._queued:
                    continue
                self._queued.add(key)
            self.queue.put((key, reason))

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                key, reason = self.queue.get(timeout=TICK)
            except queue.Empty:
                self.worker_beat = time.monotonic()
                self.idle()
                continue
            with self._lock:
                self._queued.discard(key)
            started = time.monotonic()
            try:
                outcome = self.worker.process(key)
            except Exception:  # the last line of defence; process() handles expected errors
                log.exception("unexpected error on %s", key)
                outcome = Outcome.FAILED
            if outcome not in (Outcome.UNCHANGED, Outcome.SKIPPED):
                log.info("%s %s (%s) in %.1fs", outcome, key, reason, time.monotonic() - started)
            memory.release()
            self.worker_beat = time.monotonic()

    def _sections(self) -> list[Item]:
        sections = [s for s in self.plex.sections() if s.get("title") in self.cfg.libraries and s.get("type") in KINDS]
        if not sections:
            raise LookupError(f"Plex has no movie or TV library named {', '.join(self.cfg.libraries)}")
        return sections

    def sweep(self, lookback: int = SWEEP_LOOKBACK) -> None:
        """Items added or changed since the last sweep, Maintainerr's list, and retries that are due."""
        since = int(self.store.meta("sweep_cursor", "0")) - lookback
        started = int(time.time())
        for section in self._sections():
            for kind in KINDS[str(section["type"])]:
                changed = self.plex.changed_since(str(section["key"]), kind, since)
                self.enqueue((str(i["ratingKey"]) for i in changed), "changed")
                for label in (overrides.CUSTOM_LABEL, overrides.NEXT_LABEL):
                    # Adding a label does not change an item's updatedAt, so changed_since misses it.
                    labelled = self.plex.section_items(str(section["key"]), kind, label=label)
                    self.enqueue((str(i["ratingKey"]) for i in labelled), "label")
        ignored = {
            str(i["ratingKey"])
            for section in self._sections()
            for kind in KINDS[str(section["type"])]
            for i in self.plex.section_items(str(section["key"]), kind, label=overrides.IGNORE_LABEL)
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
        """Called when the queue has been empty for a tick. Marks a full pass as finished."""
        with self._lock:
            busy = bool(self._queued) or not self.queue.empty()
        if not busy and self.store.meta("full_pending") == "1":
            self.store.set_meta("full_pending", "0")
            log.info("full pass finished")

    def resume(self) -> None:
        """A restart drops the queue. Run an unfinished full pass again; unchanged items are skipped quickly."""
        if self.store.meta("full_pending") == "1":
            log.info("resuming an unfinished full pass")
            self.full()

    def full(self) -> None:
        """Every item, and every known item that was not listed. The worker forgets those that Plex no longer has."""
        seen: set[str] = set()
        for section in self._sections():
            for kind in KINDS[str(section["type"])]:
                seen.update(str(i["ratingKey"]) for i in self.plex.section_items(str(section["key"]), kind))
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
            },
            sort_keys=True,
        )

    def _schedule(self) -> None:
        next_sweep = 0.0
        lookback = RESTART_LOOKBACK
        resumed = False
        while not self._stop.is_set():
            try:
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
            self._stop.wait(TICK)

    def healthy(self) -> bool:
        now = time.monotonic()
        sweep_limit = max(3 * self.cfg.sweep_minutes * 60, 900)
        alive = all(thread.is_alive() for thread in self.threads)
        return alive and now - self.worker_beat < WORKER_STALL and now - self.last_sweep_ok < sweep_limit

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
                if self.path != "/healthz":
                    self._reply(404, {"error": "not found"})
                    return
                ok = service.healthy()
                self._reply(
                    200 if ok else 503,
                    {"ok": ok, "queue": service.queue.qsize(), "images": service.store.counts(),
                     "dry_run": service.cfg.dry_run, "version": __version__},
                )  # fmt: skip

            def do_POST(self) -> None:
                if not hmac.compare_digest(self.path.split("?")[0].encode(), webhook_path.encode()):
                    self._reply(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
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
                self._reply(200, {"ok": True})

        return Handler

    def run(self) -> None:
        self.threads = [threading.Thread(target=t, daemon=True, name=t.__name__) for t in (self._work, self._schedule)]
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
        server.serve_forever()
        server.server_close()
        for thread in self.threads:
            thread.join(timeout=20)
