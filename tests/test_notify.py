import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from posteryard import config
from posteryard.notify import Notifier, split_urls


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
    with pytest.raises(config.ConfigError, match="address 2 ") as caught:
        config.load({"TMDB_API_KEY": "example", "NOTIFY_URLS": "ntfy://example.org/a nonsense-secret"})
    assert "nonsense-secret" not in str(caught.value)
