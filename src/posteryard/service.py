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

from posteryard import __version__, http, memory, overrides, service_collections, statuspage
from posteryard.config import Config
from posteryard.server import Item, MediaServer
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
HEARTBEAT_SECONDS = 60


def parse_webhook(content_type: str, body: bytes) -> dict[str, Any] | None:
    """Plex posts multipart/form-data with the event JSON in the `payload` field. Raises ValueError on bad JSON."""
    raw: bytes | None = None
    if content_type.startswith("application/json"):
        raw = body
    else:
        message = BytesParser(policy=policy.HTTP).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
        )
        if message.is_multipart():
            for part in message.iter_parts():
                if part.get_param("name", header="content-disposition") == "payload":
                    payload = part.get_payload(decode=True)
                    raw = payload if isinstance(payload, bytes) else None
                    break
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except RecursionError:
        raise ValueError("the payload is nested too deeply") from None
    return data if isinstance(data, dict) else None


def related_keys(item: object) -> list[str]:
    """A new episode can also need its season and show artwork."""
    if not isinstance(item, dict):
        return []
    keys = [item.get("ratingKey"), item.get("parentRatingKey"), item.get("grandparentRatingKey")]
    return [str(k) for k in keys if k]


class Service:
    def __init__(self, cfg: Config, server: MediaServer, store: Store, worker: Worker) -> None:
        self.cfg, self.server, self.store, self.worker = cfg, server, store, worker
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.worker_beat = time.monotonic()
        self.last_sweep_ok = time.monotonic()
        self.threads: list[threading.Thread] = []
        self.exit_code = 0
        self._last_heartbeat = float("-inf")

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
        """Items added or changed since the last sweep, Maintainerr's list, and retries that are due."""
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
        """Every item, and every known item that was not listed. The worker forgets those the server no longer has."""
        seen: set[str] = set()
        if self.cfg.service_collections:
            for section in self._sections():
                if section.get("type") == "show":
                    seen.update(service_collections.sync(self.server, self.worker.ctx, str(section["key"])))
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
            self.heartbeat()
            self._stop.wait(TICK)

    def heartbeat(self) -> None:
        """Call HEARTBEAT_URL while healthy, so a push monitor notices when the calls stop."""
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
            "queue": self.queue.qsize(),
            "images": self.store.counts(),
            "dry_run": self.cfg.dry_run,
            "version": __version__,
        }

    def healthy(self) -> bool:
        return bool(self.status()["ok"])

    def _watch(self, server: ThreadingHTTPServer) -> None:
        """Exit when a service thread has stopped, so the container's restart policy starts a fresh one."""
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

            def _send(self, code: int, content_type: str, data: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path == "/healthz":
                    status = service.status()
                    self._reply(200 if status["ok"] else 503, status)
                elif path == "/":
                    page = statuspage.render(
                        service.status(), service.store.recent(50), service.store.failures(), service.cfg.thumbs_dir
                    )
                    self._send(200, "text/html; charset=utf-8", page.encode())
                elif path.startswith("/recent/") and (
                    found := statuspage.thumb_path(service.cfg.thumbs_dir, path.removeprefix("/recent/"))
                ):
                    self._send(200, "image/jpeg", found.read_bytes())
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
