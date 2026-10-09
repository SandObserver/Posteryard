import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from posteryard import http
from posteryard.plex import Plex

SECRET = "example-secret-value"


def test_redact_hides_credentials() -> None:
    url = f"http://plex.example:32400/library?X-Plex-Token={SECRET}&api_key={SECRET}&type=1"
    assert SECRET not in http.redact(url)
    assert "type=1" in http.redact(url)


def test_a_malformed_url_error_never_carries_the_token() -> None:
    with pytest.raises(http.RequestError) as caught:
        http.request("GET", f"http://plex.example:32400/a b?X-Plex-Token={SECRET}", retries=1)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_plex_rejects_anything_but_a_numeric_rating_key() -> None:
    plex = Plex("http://plex.example:32400", SECRET)
    for key in ("1 2", "١٢", "../1"):
        assert plex.item(key) is None
        with pytest.raises(ValueError, match="rating key"):
            plex.children(key)


class Server:
    """A local HTTP server that answers each request with the next queued (status, headers, body)."""

    def __init__(self) -> None:
        self.answers: list[tuple[int, dict[str, str], bytes]] = []
        self.connections: set[int] = set()
        self.requests: list[tuple[str, dict[str, str]]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                server.connections.add(self.client_address[1])
                server.requests.append((self.path, dict(self.headers)))
                status, headers, body = server.answers.pop(0)
                self.send_response(status)
                for name, value in {"Content-Length": str(len(body)), **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[Server]:
    running = Server()
    yield running
    running.close()


def test_transient_failures_are_retried(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("posteryard.http.time.sleep", sleeps.append)
    server.answers = [(503, {}, b"busy"), (429, {"Retry-After": "30"}, b"slow down"), (200, {}, b"ok")]
    assert http.request("GET", f"{server.url}/a?api_key={SECRET}") == b"ok"
    assert sleeps == [2.0, 30.0]


def test_connections_are_reused(server: Server) -> None:
    server.answers = [(200, {}, b"one"), (200, {}, b"two")]
    assert http.request("GET", f"{server.url}/a") == b"one"
    assert http.request("GET", f"{server.url}/b") == b"two"
    assert len(server.connections) == 1


def test_redirects_are_followed(server: Server) -> None:
    server.answers = [(302, {"Location": "/b"}, b""), (200, {}, b"moved")]
    assert http.request("GET", f"{server.url}/a") == b"moved"


def test_plex_sends_the_token_as_a_header_and_refuses_redirects(server: Server) -> None:
    server.answers = [(301, {"Location": "http://elsewhere.example/library/sections"}, b"")]
    with pytest.raises(http.RequestError, match="refused a redirect") as caught:
        Plex(server.url, SECRET).sections()
    assert SECRET not in str(caught.value)
    [(path, headers)] = server.requests
    assert SECRET not in path
    assert headers["X-Plex-Token"] == SECRET


def test_unreachable_servers_fail_after_the_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("posteryard.http.time.sleep", lambda seconds: None)
    with pytest.raises(http.RequestError, match="NewConnectionError") as caught:
        http.request("GET", f"http://127.0.0.1:9/a?X-Plex-Token={SECRET}", retries=2)
    assert SECRET not in str(caught.value)


def test_client_errors_are_not_retried_and_hide_secrets(server: Server) -> None:
    server.answers = [(401, {}, b"denied")]
    with pytest.raises(http.HttpError) as caught:
        http.request("GET", f"{server.url}/a?X-Plex-Token={SECRET}")
    assert caught.value.status == 401
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("query", "headers"),
    [
        ("", {"X-Plex-Token": SECRET}),
        ("", {"Authorization": f"Bearer {SECRET}"}),
        ("", {"Authorization": f'MediaBrowser Token="{SECRET}"'}),
        ("", {"api-key": SECRET}),
        (f"?api_key={SECRET}", {}),
    ],
)
def test_an_error_page_that_echoes_a_credential_never_carries_it(
    server: Server, query: str, headers: dict[str, str]
) -> None:
    server.answers = [(401, {}, f"denied for {SECRET}, sent {headers}".encode())]
    with pytest.raises(http.HttpError) as caught:
        http.request("GET", f"{server.url}/a{query}", headers=headers)
    assert SECRET not in str(caught.value)
    assert "denied for ***" in str(caught.value)


def test_oversized_responses_are_refused(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "MAX_RESPONSE", 4)
    server.answers = [(200, {}, b"too long")]
    with pytest.raises(http.RequestError, match="larger than"):
        http.request("GET", f"{server.url}/a")


def test_retry_after_is_read_as_seconds_or_a_date() -> None:
    assert http.retry_after(None) == 0
    assert http.retry_after("5") == 5
    assert http.retry_after("3600") == http.RETRY_AFTER_MAX
    assert http.retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0
    assert http.retry_after("soon") == 0


def test_a_bad_header_value_never_reaches_the_error(server: Server) -> None:
    with pytest.raises(http.RequestError) as caught:
        http.request("GET", f"{server.url}/a", headers={"api-key": f"{SECRET}\nrest"})
    assert SECRET not in str(caught.value)


def test_redirects_can_be_refused(server: Server) -> None:
    server.answers = [(302, {"Location": "http://elsewhere.example/b"}, b"")]
    with pytest.raises(http.RequestError, match="refused a redirect"):
        http.request("GET", f"{server.url}/a", redirects=False)


def test_cross_host_redirects_drop_the_api_key_header() -> None:
    assert "api-key" in {h.lower() for h in http.POOL.connection_pool_kw["retries"].remove_headers_on_redirect}
