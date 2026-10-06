import http.client
import json
import logging
import threading
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from posteryard import config, service_collections
from posteryard import http as posteryard_http
from posteryard.notify import Notifier
from posteryard.server import Item
from posteryard.service import Service, other_server, parse_webhook, related_keys
from posteryard.store import Store
from posteryard.worker import Outcome


def test_multipart_webhook_payload() -> None:
    payload = {"event": "library.new", "Metadata": {"ratingKey": "7", "type": "episode"}}
    boundary = "XyZ"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload"\r\nContent-Type: application/json\r\n\r\n'
        f"{json.dumps(payload)}\r\n--{boundary}--\r\n"
    ).encode()
    assert parse_webhook(f"multipart/form-data; boundary={boundary}", body) == payload


def test_emby_multipart_webhook_payload() -> None:
    payload = {"Event": "library.new", "Item": {"Id": "245", "Type": "Episode", "SeriesId": "13", "SeasonId": "14"}}
    boundary = "4ce19780-c225-48f2-aa29-129d10f8fa16"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=utf-8\r\n"
        f"Content-Disposition: form-data; name=data\r\n\r\n{json.dumps(payload)}\r\n--{boundary}--\r\n"
    ).encode()
    assert parse_webhook(f'multipart/form-data; boundary="{boundary}"', body) == payload


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
    collections_per_library = True

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

    def item(self, rating_key: str) -> None:
        return None

    def server_id(self) -> str:
        return "plex:example"


def queued(service: Service) -> list[str]:
    with service._lock:
        entries = sorted(service.queue.queue)
        return [key for priority, _, key, _ in entries if service._queued.get(key) == priority]


def make_service(tmp_path: Path, plex: FakePlex | None = None) -> Service:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path)})
    return Service(cfg, plex or FakePlex(), Store(cfg.state_path), worker=None)  # type: ignore[arg-type]


def test_a_full_pass_stays_pending_until_the_queue_drains(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.full("daily")
    assert service.store.meta("full_pending") == "1"
    service.idle()
    assert service.store.meta("full_pending") == "1"
    while queued(service):
        service.take(0)
    service.idle()
    assert service.store.meta("full_pending") == "0"


def test_a_restart_resumes_an_unfinished_full_pass(tmp_path: Path) -> None:
    make_service(tmp_path).full("daily")
    restarted = make_service(tmp_path)
    restarted.resume()
    assert len(queued(restarted)) == 2
    finished = make_service(tmp_path)
    finished.store.set_meta("full_pending", "0")
    finished.resume()
    assert queued(finished) == []


def test_the_sweep_queues_labelled_items(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.worker = type("W", (), {"leaving_days": staticmethod(lambda: {})})()
    service.sweep()
    assert queued(service) == ["9"]


def test_an_item_that_loses_the_ignore_label_is_queued(tmp_path: Path) -> None:
    plex = FakePlex()
    service = make_service(tmp_path, plex)
    service.worker = type("W", (), {"leaving_days": staticmethod(lambda: {})})()
    plex.ignored = [{"ratingKey": "5"}]
    service.sweep()
    assert "5" not in queued(service)
    service.store.uploaded("5", "poster", "Example", "fp", "old-upload")
    plex.ignored = []
    service.sweep()
    assert "5" in queued(service)
    assert service.store.get("5", "poster") is None


class Alerts(Notifier):
    def __init__(self) -> None:
        super().__init__()
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
        service.full("daily")
    assert store.get("1", "poster") is not None
    assert queued(service) == []


def test_a_full_pass_rechecks_unlisted_items_instead_of_forgetting_them(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.store.manual("5", "poster", "Example")
    service.full("daily")
    assert sorted(queued(service)) == ["1", "2", "5"]
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
        assert queued(service) == ["7"]
        emby = b'{"Event": "library.new", "Item": {"Id": "245", "Type": "Episode"}}'
        assert post("/webhook/example-secret", emby, {"Content-Type": "application/json; charset=utf-8"}) == 200
        assert post("/webhook/example-secret", b'{"Event": "library.new", "Item": "x"}', json_type) == 200
        assert queued(service) == ["7", "245"]
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
    service.server_checked.set()
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


def test_a_webhook_item_queues_the_item_and_its_parents(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    episode: Item = {"ratingKey": "d" * 32, "parentRatingKey": "c" * 32, "grandparentRatingKey": "b" * 32}
    service.server.item = lambda key: episode if key == "d" * 32 else None  # type: ignore[method-assign, assignment]
    service.item_added("dddddddd-dddd-dddd-dddd-dddddddddddd")
    assert queued(service) == ["d" * 32, "c" * 32, "b" * 32]
    service.item_added("../etc")
    service.item_added(None)
    service.item_added("a" * 32)
    service.item_added(245)
    assert queued(service)[-2:] == ["a" * 32, "245"]


def test_webhook_items_go_before_the_full_pass(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.enqueue(["1", "2", "3"], "daily")
    service.enqueue(["4"], "changed")
    service.enqueue(["3", "5"], "webhook")
    service.enqueue(["5"], "daily")
    assert queued(service) == ["3", "5", "4", "1", "2"]
    assert [service.take(0) for _ in range(5)] == [
        ("3", "webhook"), ("5", "webhook"), ("4", "changed"), ("1", "daily"), ("2", "daily"),
    ]  # fmt: skip
    assert service.status()["queue"] == 0


def test_jellyfin_posts_json_as_text() -> None:
    body = b'{"NotificationType": "ItemAdded", "ItemId": "e58e4e34025383f58942a3e8447eb6ce", "ItemType": "Episode"}'
    payload = parse_webhook("text/plain; charset=utf-8", body)
    assert payload is not None and payload["ItemId"] == "e58e4e34025383f58942a3e8447eb6ce"


class FakeNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def alert(self, subject: str, message: str) -> None:
        self.sent.append(subject)


class TwoShowLibraries(FakePlex):
    def sections(self) -> list[dict[str, str]]:
        return [{"key": "4", "title": "TV Shows", "type": "show"}, {"key": "5", "title": "Anime", "type": "show"}]


def collections_service(tmp_path: Path, plex: FakePlex, **env: str) -> tuple[Service, list[str], FakeNotifier]:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "SERVICE_COLLECTIONS": "true",
                       "LIBRARIES": "TV Shows,Anime", **env})  # fmt: skip
    notifier = FakeNotifier()
    worker = type("W", (), {"ctx": None, "notifier": notifier})()
    return Service(cfg, plex, Store(cfg.state_path), worker), [], notifier  # type: ignore[arg-type]


def test_service_collections_wait_for_dry_run_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[str] = []

    def record(server: object, ctx: object, key: str) -> list[str]:
        synced.append(key)
        return []

    monkeypatch.setattr(service_collections, "sync", record)
    service, _, _ = collections_service(tmp_path, TwoShowLibraries())
    service._sync_collections()
    assert synced == []
    service, _, _ = collections_service(tmp_path, TwoShowLibraries(), DRY_RUN="false")
    service._sync_collections()
    assert synced == ["4", "5"]


def test_jellyfin_collections_use_one_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[str] = []

    def record(server: object, ctx: object, key: str) -> list[str]:
        synced.append(key)
        return []

    monkeypatch.setattr(service_collections, "sync", record)
    plex = TwoShowLibraries()
    plex.collections_per_library = False
    service, _, _ = collections_service(tmp_path, plex, DRY_RUN="false")
    service._sync_collections()
    assert synced == ["4"]


def test_a_failed_collection_sync_does_not_stop_the_full_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(server: object, ctx: object, key: str) -> list[str]:
        raise posteryard_http.RequestError("HTTP 404", "https://api.themoviedb.org/3/tv/1")

    monkeypatch.setattr(service_collections, "sync", fail)
    service, _, notifier = collections_service(tmp_path, TwoShowLibraries(), DRY_RUN="false")
    service.full("daily")
    assert notifier.sent == ["collections", "collections"]
    assert service.store.meta("last_full")


class OtherServer(FakePlex):
    def server_id(self) -> str:
        return "emby:other"


def test_the_service_stops_on_a_data_folder_from_another_server(tmp_path: Path) -> None:
    first = make_service(tmp_path)
    assert other_server(first.server, first.store) is None
    first.store.close()
    service = make_service(tmp_path, OtherServer())
    alerts = Alerts()
    service.worker = type("W", (), {"notifier": alerts})()
    service._schedule()
    assert service.exit_code == 2
    assert service._stop.is_set() and not service.server_checked.is_set()
    assert alerts.sent and "plex:example" in alerts.sent[0]


def test_the_worker_waits_for_the_server_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("posteryard.service.TICK", 0.01)
    service = make_service(tmp_path)
    service.enqueue(["1"], "test")
    service.worker_beat = 0.0
    thread = threading.Thread(target=service._work, daemon=True)
    thread.start()
    time.sleep(0.1)
    service._stop.set()
    thread.join(timeout=5)
    assert queued(service) == ["1"]
    assert service.worker_beat > 0


class Messages(Notifier):
    def __init__(self) -> None:
        super().__init__()
        self.summaries: list[str] = []
        self.new: list[tuple[list[str], bytes | None]] = []

    def summary(self, message: str, *, failed: bool) -> None:
        self.summaries.append(message)

    def new_posters(self, names: Sequence[str], poster: bytes | None = None) -> None:
        self.new.append((list(names), poster))


def messages_service(tmp_path: Path) -> tuple[Service, Messages]:
    notifier = Messages()
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "DRY_RUN": "false"})
    worker = type("W", (), {"notifier": notifier, "fresh": []})()
    return Service(cfg, FakePlex(), Store(cfg.state_path), worker), notifier  # type: ignore[arg-type]


def test_a_full_check_ends_with_a_summary(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    service, notifier = messages_service(tmp_path)
    service.full("daily")
    service._tally.update({Outcome.UPLOADED: 2, Outcome.UNCHANGED: 5, Outcome.FAILED: 1})
    while queued(service):
        service.take(0)
    with caplog.at_level(logging.INFO):
        service.idle()
    done = next(r for r in caplog.records if r.getMessage() == "full check done")
    assert (done.uploaded, done.unchanged, done.failed) == (2, 5, 1)  # type: ignore[attr-defined]
    assert notifier.summaries[0].startswith("Checked 8 items in ")
    assert notifier.summaries[0].endswith(": 2 updated, 1 failed.")


def test_a_quiet_full_check_sends_no_summary(tmp_path: Path) -> None:
    service, notifier = messages_service(tmp_path)
    service.full("daily")
    service._tally.update({Outcome.UNCHANGED: 7})
    while queued(service):
        service.take(0)
    service.idle()
    assert notifier.summaries == []


def test_new_posters_are_grouped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, notifier = messages_service(tmp_path)
    service.collect_new([("Dune", b"dune")])
    service.announce_new(idle=False)
    assert notifier.new == []
    service.announce_new(idle=True)
    assert notifier.new == [(["Dune"], b"dune")]
    service.collect_new([("Severance · Season 2", b"s2")])
    service.collect_new([("The Batman", b"batman")])
    service.announce_new(idle=True)
    assert len(notifier.new) == 1
    monkeypatch.setattr(service, "_last_new", float("-inf"))
    service.announce_new(idle=True)
    assert notifier.new[1] == (["Severance · Season 2", "The Batman"], None)


def test_a_missing_library_is_named(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    service = make_service(tmp_path)
    with caplog.at_level(logging.INFO):
        service.connect()
    connected, missing = caplog.records
    assert connected.libraries == "Movies"  # type: ignore[attr-defined]
    assert (missing.getMessage(), missing.library) == ("library not found", "TV Shows")  # type: ignore[attr-defined]


def test_the_startup_block_lists_the_settings(tmp_path: Path) -> None:
    service, _ = messages_service(tmp_path)
    lines = service.banner().splitlines()
    assert lines[1].startswith("  Posteryard ")
    assert "  Server     Plex, libraries Movies, TV Shows" in lines
    assert "  Alerts     off, NOTIFY_URLS is empty" in lines
    assert not any(line.startswith("  Mode") for line in lines)
    assert any(line.startswith("  Mode") for line in make_service(tmp_path).banner().splitlines())
