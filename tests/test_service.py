import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from posteryard import config
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
    finally:
        server.shutdown()
        server.server_close()
