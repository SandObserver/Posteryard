import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from posteryard import config, statuspage
from posteryard.service import Service, parse_webhook, related_keys
from posteryard.store import Store


def test_multipart_webhook_payload() -> None:
    payload = {"event": "library.new", "Metadata": {"ratingKey": "7", "type": "episode"}}
    boundary = "XyZ"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload"\r\nContent-Type: application/json\r\n\r\n'
        f"{json.dumps(payload)}\r\n--{boundary}--\r\n"
    ).encode()
    assert parse_webhook(f"multipart/form-data; boundary={boundary}", body) == payload


def test_json_webhook_and_garbage() -> None:
    assert parse_webhook("application/json", b'{"event": "media.play"}') == {"event": "media.play"}
    assert parse_webhook("text/plain", b"hello") is None


def test_a_webhook_body_that_is_not_an_object_is_refused() -> None:
    assert parse_webhook("application/json", b"[1, 2]") is None
    assert parse_webhook("application/json", b'"text"') is None
    with pytest.raises(ValueError):
        parse_webhook("application/json", b"\xff\xfe")
    with pytest.raises(ValueError, match="nested"):
        parse_webhook("application/json", b"[" * 100_000 + b"]" * 100_000)


def test_an_episode_brings_its_season_and_show() -> None:
    assert related_keys({"ratingKey": 7, "parentRatingKey": 6, "grandparentRatingKey": 5}) == ["7", "6", "5"]
    assert related_keys({"ratingKey": "9"}) == ["9"]
    assert related_keys("x") == []


class FakePlex:
    name = "Plex"
    url = "http://plex.example:32400"

    def __init__(self) -> None:
        self.ignored: list[dict[str, str]] = []

    def sections(self) -> list[dict[str, str]]:
        return [{"key": "3", "title": "Movies", "type": "movie"}]

    def section_items(self, section: str, kind: str, **filters: str) -> list[dict[str, str]]:
        if filters.get("label") == "posteryard-next":
            return [{"ratingKey": "9"}]
        if filters.get("label") == "posteryard-ignore":
            return self.ignored
        if filters:
            return []
        return [{"ratingKey": "1"}, {"ratingKey": "2"}]

    def changed_since(self, section: str, kind: str, since: int) -> list[dict[str, str]]:
        return []


def make_service(tmp_path: Path, plex: FakePlex | None = None) -> Service:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path)})
    return Service(cfg, plex or FakePlex(), Store(cfg.state_path), worker=None)  # type: ignore[arg-type]


def test_a_full_pass_stays_pending_until_the_queue_drains(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.full()
    assert service.store.meta("full_pending") == "1"
    service.idle()
    assert service.store.meta("full_pending") == "1"
    while not service.queue.empty():
        key, _ = service.queue.get()
        service._queued.discard(key)
    service.idle()
    assert service.store.meta("full_pending") == "0"


def test_a_restart_resumes_an_unfinished_full_pass(tmp_path: Path) -> None:
    make_service(tmp_path).full()
    restarted = make_service(tmp_path)
    restarted.resume()
    assert restarted.queue.qsize() == 2
    finished = make_service(tmp_path)
    finished.store.set_meta("full_pending", "0")
    finished.resume()
    assert finished.queue.empty()


def test_the_sweep_queues_labelled_items(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.worker = type("W", (), {"leaving_days": staticmethod(lambda: {})})()
    service.sweep()
    queued = [service.queue.get()[0] for _ in range(service.queue.qsize())]
    assert queued == ["9"]


def test_an_item_that_loses_the_ignore_label_is_queued(tmp_path: Path) -> None:
    plex = FakePlex()
    service = make_service(tmp_path, plex)
    service.worker = type("W", (), {"leaving_days": staticmethod(lambda: {})})()
    plex.ignored = [{"ratingKey": "5"}]
    service.sweep()
    assert "5" not in [service.queue.get()[0] for _ in range(service.queue.qsize())]
    service.store.uploaded("5", "poster", "Example", "fp", "old-upload")
    plex.ignored = []
    service.sweep()
    assert "5" in [service.queue.get()[0] for _ in range(service.queue.qsize())]
    assert service.store.get("5", "poster") is None


class Alerts:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def alert(self, subject: str, message: str) -> None:
        self.sent.append(message)


class BrokenPlex(FakePlex):
    def sections(self) -> list[dict[str, str]]:
        raise AttributeError("unexpected answer")


def test_the_schedule_survives_an_unexpected_error(tmp_path: Path) -> None:
    service = make_service(tmp_path, BrokenPlex())
    alerts = Alerts()
    service.worker = type("W", (), {"notifier": alerts})()
    thread = threading.Thread(target=service._schedule, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not alerts.sent and time.monotonic() < deadline:
        time.sleep(0.05)
    assert thread.is_alive()
    assert alerts.sent and "unexpected answer" in alerts.sent[0]
    service._stop.set()
    thread.join(timeout=5)


def test_a_dead_thread_is_unhealthy(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    assert service.healthy()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    service.threads = [dead]
    assert not service.healthy()


def test_no_matching_library_stops_the_pass_and_keeps_records(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.manual("1", "poster", "Example")
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "PLEX_LIBRARIES": "Movie"})
    service = Service(cfg, FakePlex(), store, worker=None)  # type: ignore[arg-type]
    with pytest.raises(LookupError, match="Movie"):
        service.full()
    assert store.get("1", "poster") is not None
    assert service.queue.empty()


def test_a_full_pass_rechecks_unlisted_items_instead_of_forgetting_them(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.store.manual("5", "poster", "Example")
    service.full()
    assert sorted(key for key, _ in service.queue.queue) == ["1", "2", "5"]
    assert service.store.get("5", "poster") is not None


def test_the_webhook_server_answers_malformed_requests(tmp_path: Path) -> None:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "WEBHOOK_SECRET": "example-secret"})
    service = Service(cfg, FakePlex(), Store(cfg.state_path), worker=None)  # type: ignore[arg-type]
    server = ThreadingHTTPServer(("127.0.0.1", 0), service.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def post(path: str, body: bytes, headers: dict[str, str]) -> int:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", path, body, headers)
        status = connection.getresponse().status
        connection.close()
        return status

    json_type = {"Content-Type": "application/json"}
    good = b'{"event": "library.new", "Metadata": {"ratingKey": "7"}}'
    try:
        assert post("/webhook/wrong", good, json_type) == 404
        assert post("/webhook/example-secret", b"[1]", json_type) == 200
        assert post("/webhook/example-secret", b"{", json_type) == 400
        assert post("/webhook/example-secret", b'{"event": "library.new", "Metadata": "x"}', json_type) == 200
        assert post("/webhook/example-secret", good, {**json_type, "Content-Length": "abc"}) == 413
        assert post("/webhook/example-secret", good, json_type) == 200
        assert [key for key, _ in service.queue.queue] == ["7"]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("HEAD", "/healthz")
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_status_explains_health(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    status = service.status()
    assert status["ok"] is True
    assert set(status["checks"]) == {"threads_running", "worker_responsive", "sweep_recent"}
    service.last_sweep_ok -= 10_000
    status = service.status()
    assert status["ok"] is False and status["checks"]["sweep_recent"] is False


def test_heartbeat_is_called_only_while_healthy(tmp_path: Path) -> None:
    calls: list[str] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{receiver.server_address[1]}/api/push/example?status=up"
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "HEARTBEAT_URL": url})
    service = Service(cfg, FakePlex(), Store(cfg.state_path), worker=None)  # type: ignore[arg-type]
    try:
        service.heartbeat()
        service.heartbeat()
        assert calls == ["/api/push/example?status=up"]
        service._last_heartbeat = float("-inf")
        service.last_sweep_ok -= 10_000
        service.heartbeat()
        assert len(calls) == 1
    finally:
        receiver.shutdown()
        receiver.server_close()


def test_a_bad_heartbeat_url_is_rejected() -> None:
    with pytest.raises(config.ConfigError, match="HEARTBEAT_URL"):
        config.load({"TMDB_API_KEY": "example", "HEARTBEAT_URL": "kuma.example/api/push/x"})


def test_the_watchdog_stops_the_server_when_a_thread_dies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("posteryard.service.TICK", 0.01)
    service = make_service(tmp_path)
    alerts = Alerts()
    service.worker = type("W", (), {"notifier": alerts})()
    dead = threading.Thread(target=lambda: None, name="worker")
    dead.start()
    dead.join()
    service.threads = [dead]
    stopped: list[bool] = []
    service._watch(type("S", (), {"shutdown": lambda self: stopped.append(True)})())
    assert stopped == [True]
    assert service.exit_code == 1
    assert alerts.sent and "worker" in alerts.sent[0]


def test_the_worker_loop_processes_each_queued_key_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("posteryard.service.TICK", 0.01)
    service = make_service(tmp_path)
    done: list[str] = []

    def process(key: str) -> str:
        done.append(key)
        if key == "2":
            raise RuntimeError("unexpected")
        return "uploaded"

    service.worker = type("W", (), {"process": staticmethod(process)})()
    service.enqueue(["1", "2", "1"], "test")
    thread = threading.Thread(target=service._work, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while len(done) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    service._stop.set()
    thread.join(timeout=5)
    assert done == ["1", "2"]
    assert not thread.is_alive()


def test_the_status_page_and_its_thumbnails(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.store.uploaded("7", "poster", "Example <Movie>", "abc", "upload-1")
    service.store.failed("8", "item", "Broken Show", "TMDB has no textless art")
    statuspage.save_thumb(service.cfg.thumbs_dir, "7", "poster", Image.new("RGB", (1000, 1500)))
    server = ThreadingHTTPServer(("127.0.0.1", 0), service.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path: str) -> tuple[int, str, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        result = response.status, response.getheader("Content-Type") or "", response.read()
        connection.close()
        return result

    try:
        status, kind, body = get("/")
        page = body.decode()
        assert status == 200 and kind.startswith("text/html")
        assert "Example &lt;Movie&gt;" in page
        assert "Broken Show" in page and "TMDB has no textless art" in page
        assert 'src="recent/7-poster.jpg"' in page
        status, kind, body = get("/recent/7-poster.jpg")
        assert (status, kind, body[:2]) == (200, "image/jpeg", b"\xff\xd8")
        assert get("/recent/..%2Fstate.db")[0] == 404
        assert get("/recent/9-poster.jpg")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_only_the_newest_thumbnails_are_kept(tmp_path: Path) -> None:
    for n in range(statuspage.KEEP + 5):
        statuspage.save_thumb(tmp_path, str(n), "poster", Image.new("RGB", (10, 15)))
    assert len(list(tmp_path.glob("*.jpg"))) == statuspage.KEEP
