import json
import logging
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from posteryard import config
from posteryard.notify import Event, Notifier, invalid_urls, split_urls
from posteryard.store import Store


class Receiver(BaseHTTPRequestHandler):
    received: list[tuple[str, dict[str, str], bytes]]

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.received.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def receiver() -> Iterator[tuple[str, list[tuple[str, dict[str, str], bytes]]]]:
    received: list[tuple[str, dict[str, str], bytes]] = []
    handler = type("Handler", (Receiver,), {"received": received})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_address[1]}", received
    server.shutdown()
    server.server_close()


def test_alerts_reach_every_service_once_per_quiet_period(receiver: Any) -> None:
    address, received = receiver
    notifier = Notifier([f"json://{address}/one", f"json://{address}/two"])
    notifier.alert("Plex", "Plex is down")
    notifier.alert("Plex", "Plex is still down")
    assert sorted(path for path, _, _ in received) == ["/one", "/two"]
    hook = json.loads(received[0][2])
    assert hook["title"] == "Posteryard: Plex"
    assert hook["message"] == "Plex is down"


def messages(received: list[tuple[str, dict[str, str], bytes]]) -> list[dict[str, Any]]:
    return [json.loads(body) for _, _, body in received]


def test_a_resolved_problem_says_so_once(receiver: Any) -> None:
    address, received = receiver
    notifier = Notifier([f"json://{address}/hook"])
    notifier.resolve("TMDB", "TMDB works again.")
    notifier.alert("TMDB", "TMDB is down")
    notifier.resolve("TMDB", "TMDB works again.")
    notifier.resolve("TMDB", "TMDB works again.")
    assert [(m["message"], m["type"]) for m in messages(received)] == [
        ("TMDB is down", "warning"),
        ("TMDB works again.", "success"),
    ]


def test_open_problems_survive_a_restart(tmp_path: Path, receiver: Any) -> None:
    address, received = receiver
    store = Store(tmp_path / "state.db")
    Notifier([f"json://{address}/hook"], state=store).alert("restart", "Posteryard restarts")
    restarted = Notifier([f"json://{address}/hook"], state=store)
    restarted.alert("restart", "Posteryard restarts")
    restarted.resolve("restart", "Posteryard is running again.")
    assert [m["message"] for m in messages(received)] == ["Posteryard restarts", "Posteryard is running again."]


def test_only_chosen_events_are_sent(receiver: Any) -> None:
    address, received = receiver
    quiet = Notifier([f"json://{address}/hook"], events=frozenset({Event.NEW}))
    quiet.alert("TMDB", "TMDB is down")
    quiet.summary("Checked 5 items: 1 updated.", failed=False)
    assert received == []
    Notifier([f"json://{address}/hook"]).new_posters(["Dune"])
    assert received == []


def test_new_posters_come_as_one_short_message(receiver: Any) -> None:
    address, received = receiver
    notifier = Notifier([f"json://{address}/hook"], events=frozenset({Event.NEW, Event.SUMMARY}))
    notifier.new_posters([f"Title {n}" for n in range(12)])
    notifier.new_posters(["Dune"], b"\xff\xd8poster")
    notifier.summary("Checked 5 items: 1 updated, 1 failed.", failed=True)
    many, one, summary = messages(received)
    assert many["title"] == "Posteryard: 12 new posters"
    assert many["message"].splitlines()[-2:] == ["Title 9", "and 2 more"]
    assert one["title"] == "Posteryard: new poster"
    assert one["attachments"][0]["mimetype"] == "image/jpeg"
    assert (summary["title"], summary["type"]) == ("Posteryard: daily check", "warning")


def test_a_flapping_cause_sends_one_alert_and_one_recovery(receiver: Any) -> None:
    address, received = receiver
    notifier = Notifier([f"json://{address}/hook"])
    for n in range(3):
        notifier.alert("TMDB", f"Title {n} failed 3 times")
        notifier.resolve("TMDB", "TMDB works again.")
    assert [m["message"] for m in messages(received)] == ["Title 0 failed 3 times", "TMDB works again."]


def test_an_undelivered_alert_is_tried_again_soon(
    tmp_path: Path, receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    address, received = receiver
    store = Store(tmp_path / "state.db")
    down = Notifier(["json://127.0.0.1:9/hook"], state=store)
    down.alert("Plex", "Plex is down")
    down.resolve("Plex", "Plex works again.")
    up = Notifier([f"json://{address}/hook"], state=store)
    up.alert("Plex", "Plex is down")
    assert received == []
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 601)
    up.alert("Plex", "Plex is down")
    assert [m["message"] for m in messages(received)] == ["Plex is down"]


def test_unreadable_alert_state_starts_empty(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    for raw in ("not json", "[]", '{"muted": {"Plex": "soon"}, "open": []}'):
        store.set_meta("alert_state", raw)
        Notifier(state=store).alert("Plex", "Plex is down")


def test_a_bad_address_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    level = logging.getLogger("apprise").level
    with caplog.at_level(logging.DEBUG):
        assert invalid_urls(["foo://SECRET@example.org/topic"]) == [1]
    assert "SECRET" not in caplog.text
    assert logging.getLogger("apprise").level == level


def test_send_reports_a_failed_service() -> None:
    assert not Notifier(["json://127.0.0.1:9/hook"]).send("test", "hello")
    assert not Notifier().configured


def test_removed_ntfy_settings_are_refused() -> None:
    with pytest.raises(config.ConfigError, match=r"NTFY_URL, NTFY_TOPIC no longer exist\. Set NOTIFY_URLS=ntfys://"):
        config.load({"TMDB_API_KEY": "example", "NTFY_URL": "http://ntfy", "NTFY_TOPIC": "alerts"})


def test_notify_urls_are_split_and_checked() -> None:
    assert split_urls(" ntfy://example.org/a, discord://1/abc  json://example.org ") == [
        "ntfy://example.org/a",
        "discord://1/abc",
        "json://example.org",
    ]
    cfg = config.load({"TMDB_API_KEY": "example", "NOTIFY_URLS": "ntfy://example.org/a"})
    assert cfg.notify_urls == ("ntfy://example.org/a",)
    assert cfg.notify_events == {Event.PROBLEMS}
    with pytest.raises(config.ConfigError, match="address 2 ") as caught:
        config.load({"TMDB_API_KEY": "example", "NOTIFY_URLS": "ntfy://example.org/a nonsense-secret"})
    assert "nonsense-secret" not in str(caught.value)


def test_notify_events_are_checked() -> None:
    cfg = config.load({"TMDB_API_KEY": "example", "NOTIFY_EVENTS": "Problems, new,summary"})
    assert cfg.notify_events == {Event.PROBLEMS, Event.NEW, Event.SUMMARY}
    assert config.load({"TMDB_API_KEY": "example", "NOTIFY_EVENTS": " "}).notify_events == {Event.PROBLEMS}
    with pytest.raises(config.ConfigError, match="not everything"):
        config.load({"TMDB_API_KEY": "example", "NOTIFY_EVENTS": "problems,everything"})
