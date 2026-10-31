import contextlib
import gzip
import socket
import threading
import time
import tracemalloc
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from posteryard import __version__, http
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
    """A local HTTP server that answers each request with the next queued (status, headers, body). Status 0 hangs up."""

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
                if not status:
                    self.close_connection = True
                    return
                self.send_response(status)
                for name, value in {"Content-Length": str(len(body)), **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
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


def test_a_dropped_connection_is_tried_again_at_once_without_a_warning(
    server: Server, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("posteryard.http.time.sleep", sleeps.append)
    server.answers = [(0, {}, b""), (200, {}, b"ok")]
    assert http.request("GET", f"{server.url}/a") == b"ok"
    assert sleeps == []
    assert "retrying request" not in caplog.text


def test_connections_are_reused(server: Server) -> None:
    server.answers = [(200, {}, b"one"), (200, {}, b"two")]
    assert http.request("GET", f"{server.url}/a") == b"one"
    assert http.request("GET", f"{server.url}/b") == b"two"
    assert len(server.connections) == 1


def test_requests_name_posteryard_its_version_and_its_home(server: Server) -> None:
    server.answers = [(200, {}, b"ok")]
    http.request("GET", f"{server.url}/a")
    [(_, headers)] = server.requests
    assert headers["User-Agent"] == f"Posteryard/{__version__} (+https://github.com/SandObserver/Posteryard)"


def test_responses_are_asked_for_gzip_and_read_unpacked(server: Server) -> None:
    server.answers = [(200, {"Content-Encoding": "gzip"}, gzip.compress(b"{}" * 1000))]
    assert http.request("GET", f"{server.url}/a") == b"{}" * 1000
    [(_, headers)] = server.requests
    assert headers["Accept-Encoding"] == "gzip"


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


def test_an_html_error_page_is_left_out_of_the_reason(server: Server) -> None:
    server.answers = [(404, {}, b"<!DOCTYPE html>\n<html>\n<title>File Not Found</title>\n</html>")]
    with pytest.raises(http.HttpError) as caught:
        http.request("GET", f"{server.url}/t/p/original/gone.jpg")
    assert str(caught.value) == f"HTTP 404 for {server.url}/t/p/original/gone.jpg"


def test_a_text_error_answer_is_kept_on_one_line(server: Server) -> None:
    server.answers = [(400, {}, b'{\n  "status_message": "Invalid id"\n}')]
    with pytest.raises(http.HttpError, match=r'HTTP 400: \{ "status_message": "Invalid id" \} for '):
        http.request("GET", f"{server.url}/a")


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


def test_a_response_sent_one_byte_at_a_time_is_cut_off() -> None:
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def drip() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
            while not stop.wait(0.05):
                conn.sendall(b"x")

    threading.Thread(target=drip, daemon=True).start()
    started = time.monotonic()
    try:
        with pytest.raises(http.RequestError, match="too slow"):
            http.request("GET", f"http://127.0.0.1:{listener.getsockname()[1]}/a", timeout=0.2, retries=1)
    finally:
        stop.set()
        listener.close()
    assert time.monotonic() - started < 3


@contextlib.contextmanager
def dripping_server(head: bytes, drip: bytes, *, moved: bytes = b"") -> Iterator[str]:
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def answer(conn: socket.socket) -> None:
        with conn, contextlib.suppress(OSError):
            if conn.recv(65536).startswith(b"GET /moved"):
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(moved), moved))
                return
            conn.sendall(head)
            while not stop.wait(0.05):
                conn.sendall(drip)

    def accept() -> None:
        with contextlib.suppress(OSError):
            while True:
                threading.Thread(target=answer, args=(listener.accept()[0],), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        stop.set()
        listener.close()


@pytest.mark.parametrize(
    ("head", "drip"),
    [
        (b"HTTP/1.1 200 OK\r\nX-Slow: ", b"a"),
        (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", b"0"),
    ],
    ids=["headers", "chunk size"],
)
def test_a_server_that_never_finishes_a_line_is_cut_off(head: bytes, drip: bytes) -> None:
    started = time.monotonic()
    with dripping_server(head, drip) as url, pytest.raises(http.RequestError, match="too slow"):
        http.request("GET", f"{url}/a", timeout=0.2, retries=1)
    assert time.monotonic() - started < 3


def test_the_body_of_a_redirect_is_never_read() -> None:
    head = b"HTTP/1.1 302 Found\r\nLocation: /moved\r\nTransfer-Encoding: chunked\r\n\r\n"
    started = time.monotonic()
    with dripping_server(head, b"1\r\nx\r\n", moved=b"here") as url:
        assert http.request("GET", f"{url}/a", timeout=0.2) == b"here"
    assert time.monotonic() - started < 3


EMPTY_BLOCK = b"\x00\x00\x00\xff\xff"
GZIP_HEADER = gzip.compress(b"")[:10]


@pytest.mark.parametrize(
    ("body", "unpacked"),
    [
        (gzip.compress(b"one") + gzip.compress(b"two"), b"onetwo"),
        (gzip.compress(b"one") + b"\x00\x00 trailing junk", b"one"),
        (b"", b""),
    ],
    ids=["members", "trailing junk", "empty"],
)
def test_gzip_responses_are_unpacked(server: Server, body: bytes, unpacked: bytes) -> None:
    server.answers = [(200, {"Content-Encoding": "gzip"}, body)]
    assert http.request("GET", f"{server.url}/a") == unpacked


@pytest.mark.parametrize(
    ("encoding", "body", "error"),
    [
        ("br", b"anything", "unsupported compression 'br'"),
        ("gzip, gzip", gzip.compress(gzip.compress(b"x")), "unsupported compression"),
        ("gzip", gzip.compress(b"0" * 1000)[:-12], "cut off"),
        ("gzip", GZIP_HEADER + b"not deflate data", "broken compressed response"),
    ],
    ids=["brotli", "nested", "cut off", "broken"],
)
def test_bad_compressed_responses_are_refused(server: Server, encoding: str, body: bytes, error: str) -> None:
    server.answers = [(200, {"Content-Encoding": encoding}, body)]
    with pytest.raises(http.RequestError, match=error):
        http.request("GET", f"{server.url}/a", retries=1)


@pytest.mark.parametrize(
    "body",
    [gzip.compress(b"0" * 1_000_000), GZIP_HEADER + EMPTY_BLOCK * 1000 + gzip.compress(b"")[10:]],
    ids=["unpacked", "packed"],
)
def test_the_size_limit_counts_both_packed_and_unpacked_bytes(
    server: Server, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setattr(http, "MAX_RESPONSE", 1000)
    server.answers = [(200, {"Content-Encoding": "gzip"}, body)]
    with pytest.raises(http.RequestError, match="larger than"):
        http.request("GET", f"{server.url}/a")


def test_a_gzip_bomb_never_unpacks_past_the_limit() -> None:
    assert len(http._Gunzip().feed(gzip.compress(b"0" * 10_000_000), 1000)) == 1000


def test_a_compressed_response_that_never_unpacks_is_cut_off() -> None:
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def stream() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nConnection: close\r\n\r\n" + GZIP_HEADER)
            try:
                while not stop.is_set():
                    conn.sendall(EMPTY_BLOCK * 100)
            except OSError:
                pass

    threading.Thread(target=stream, daemon=True).start()
    started = time.monotonic()
    try:
        with pytest.raises(http.RequestError, match=r"too slow|larger than"):
            http.request("GET", f"http://127.0.0.1:{listener.getsockname()[1]}/a", timeout=0.2, retries=1)
    finally:
        stop.set()
        listener.close()
    assert time.monotonic() - started < 3


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


def test_cross_host_redirects_drop_credentials(server: Server) -> None:
    other = Server()
    try:
        credentials = {"api-key": SECRET, "Authorization": f"Bearer {SECRET}", "X-Plex-Token": SECRET}
        server.answers = [(302, {"Location": "/b"}, b""), (302, {"Location": f"{other.url}/c"}, b"")]
        other.answers = [(200, {}, b"moved")]
        assert http.request("GET", f"{server.url}/a", headers={**credentials, "Accept": "image/*"}) == b"moved"
        assert all(SECRET in str(headers) for _, headers in server.requests)
        [(_, headers)] = other.requests
        assert SECRET not in str(headers)
        assert headers["Accept"] == "image/*"
    finally:
        other.close()


@pytest.mark.parametrize(
    ("location", "error"),
    [("file:///etc/passwd", "refused a redirect"), ("/a", "too many redirects")],
    ids=["scheme", "loop"],
)
def test_bad_redirects_are_refused(server: Server, location: str, error: str) -> None:
    server.answers = [(302, {"Location": location}, b"")] * (http.MAX_REDIRECTS + 1)
    with pytest.raises(http.RequestError, match=error):
        http.request("GET", f"{server.url}/a")


def test_a_redirect_python_reads_as_another_host_is_refused(server: Server) -> None:
    other = Server()
    try:
        port = server.url.rsplit(":", 1)[1]
        server.answers = [(302, {"Location": f"{other.url}\\@127.0.0.1:{port}/b"}, b"")]
        with pytest.raises(http.RedirectError, match="refused a redirect"):
            http.request("GET", f"{server.url}/a", headers={"Authorization": f"Bearer {SECRET}"})
        assert other.requests == []
    finally:
        other.close()


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("http://host/a", "http://host:80/b"),
        ("https://HOST/a", "https://host:443/b"),
        ("http://bücher.example/a", "http://xn--bcher-kva.example/b"),
        ("http://[::1]:8096/a", "http://[::1]:8096/b"),
    ],
    ids=["http port", "https port", "idn", "ipv6"],
)
def test_one_origin_written_two_ways_is_the_same_host(one: str, other: str) -> None:
    assert http._origin(one) is not None
    assert http._origin(one) == http._origin(other)


@pytest.mark.parametrize("location", ["http://example.test:abc/", "http://[::1/x"], ids=["port", "ipv6"])
def test_an_unreadable_redirect_is_refused(server: Server, location: str) -> None:
    server.answers = [(302, {"Location": location}, b"")]
    with pytest.raises(http.RedirectError, match="refused a redirect"):
        http.request("GET", f"{server.url}/a")


@pytest.mark.parametrize("untrusted", [True, False], ids=["guide", "server"])
def test_connecting_to_many_silent_addresses_stops_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch, untrusted: bool
) -> None:
    waits: list[float] = []

    def lookup(host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"192.0.2.{n}", port)) for n in range(1, 21)]

    def silent(address: tuple[str, int], timeout: float, *args: Any, **kwargs: Any) -> socket.socket:
        waits.append(timeout)
        time.sleep(timeout)
        raise TimeoutError

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr("urllib3.util.connection.create_connection", silent)
    started = time.monotonic()
    with pytest.raises(http.RequestError):
        http.request("GET", "http://silent.example/a", timeout=0.25, untrusted=untrusted)
    assert time.monotonic() - started < 2
    assert sum(waits) <= 0.25 * http.DEADLINE_FACTOR + 0.1


def test_retries_stop_at_the_deadline(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("posteryard.http.time.sleep", sleeps.append)
    server.answers = [(503, {"Retry-After": "60"}, b"busy")]
    with pytest.raises(http.HttpError, match="HTTP 503"):
        http.request("GET", f"{server.url}/a", timeout=1)
    assert sleeps == []


def test_a_large_error_answer_is_read_only_in_part(server: Server) -> None:
    server.answers = [(404, {}, b"ab " * 1_000_000), (200, {}, b"ok")]
    tracemalloc.start()
    try:
        with pytest.raises(http.HttpError, match=r"HTTP 404: ab ab ab") as caught:
            http.request("GET", f"{server.url}/a")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    assert len(str(caught.value)) < 300
    assert http.request("GET", f"{server.url}/b") == b"ok"


def test_a_credential_cut_by_the_error_excerpt_never_shows(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "ERROR_EXCERPT", 12)
    server.answers = [(401, {}, f"denied for {SECRET}".encode())]
    with pytest.raises(http.HttpError) as caught:
        http.request("GET", f"{server.url}/a", headers={"api-key": SECRET})
    assert str(caught.value) == f"HTTP 401: denied for for {server.url}/a"


def test_redact_hides_logins_in_guide_addresses() -> None:
    url = "http://me:hunter2@guide.example:8080/xmltv.php?username=me&password=hunter2&type=xml"
    redacted = http.redact(url)
    assert "hunter2" not in redacted
    assert "me" not in redacted.split("@")[0].removeprefix("http://")
    assert redacted.startswith("http://***@guide.example:8080/xmltv.php?")
    assert "type=xml" in redacted
    assert http.hide("echo hunter2 to me", http.secrets(url, {})) == "echo *** to me"


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("127.0.0.1", "loopback"),
        ("::1", "loopback"),
        ("::ffff:127.0.0.1", "loopback"),
        ("169.254.169.254", "link-local"),
        ("fe80::1", "link-local"),
        ("224.0.0.1", "multicast"),
        ("240.0.0.1", "reserved"),
        ("0.0.0.0", "unspecified"),
        ("100.100.100.200", "metadata service"),
        ("fd00:ec2::254", "metadata service"),
        ("::ffff:100.100.100.200", "metadata service"),
        ("192.168.1.20", ""),
        ("10.0.0.5", ""),
        ("100.64.0.7", ""),
        ("93.184.215.14", ""),
    ],
)
def test_guide_content_may_name_lan_and_public_hosts_only(address: str, reason: str) -> None:
    assert reason in http.refused(address)
    assert bool(http.refused(address)) == bool(reason)


@pytest.mark.parametrize("path", ["/x", "\\@example.com/x"])
def test_untrusted_addresses_never_reach_this_machine(
    server: Server, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr("posteryard.http.time.sleep", lambda seconds: pytest.fail("retried a refused address"))
    with pytest.raises(http.RequestError, match=r"refused 127\.0\.0\.1 as a loopback address"):
        http.request("GET", f"{server.url}{path}", untrusted=True)
    assert server.requests == []


def test_untrusted_redirects_are_checked_too(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "refused", lambda address: "" if address == "127.0.0.1" else "a link-local address")
    server.answers = [(302, {"Location": "http://169.254.169.254/latest/meta-data"}, b"")]
    with pytest.raises(http.RequestError, match=r"refused 169\.254\.169\.254 as a link-local address"):
        http.request("GET", f"{server.url}/a", untrusted=True)
    assert [path for path, _ in server.requests] == ["/a"]


def test_a_trusted_address_on_this_machine_still_has_its_redirects_checked(server: Server) -> None:
    server.answers = [(302, {"Location": "/b"}, b""), (200, {}, b"unused")]
    with pytest.raises(http.RequestError, match=r"refused 127\.0\.0\.1 as a loopback address"):
        http.get_checked_redirects(f"{server.url}/a", timeout=5)
    assert [path for path, _ in server.requests] == ["/a"]


def test_a_redirect_without_an_address_is_refused(server: Server) -> None:
    server.answers = [(302, {}, b"")]
    with pytest.raises(http.RedirectError, match="refused a redirect"):
        http.get_checked_redirects(f"{server.url}/a", timeout=5)


def test_a_trusted_address_without_a_redirect_is_read_as_usual(server: Server) -> None:
    server.answers = [(200, {}, b"guide")]
    assert http.get_checked_redirects(f"{server.url}/a", timeout=5) == b"guide"


def test_untrusted_hosts_are_looked_up_once_and_connected_to_as_checked(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = socket.getaddrinfo
    port = int(server.url.rsplit(":", 1)[1])
    answers = iter(["127.0.0.1", "169.254.169.254"])
    lookups: list[str] = []

    def lookup(host: str, *args: Any, **kwargs: Any) -> Any:
        if host != "guide.example":
            return real(host, *args, **kwargs)
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(http, "refused", lambda address: "" if address == "127.0.0.1" else "a link-local address")
    server.answers = [(200, {}, b"picture")]
    assert http.request("GET", f"http://guide.example:{port}/a", untrusted=True) == b"picture"
    assert lookups == ["guide.example"]
    assert server.requests[0][1]["Host"] == f"guide.example:{port}"


def test_untrusted_hosts_try_each_checked_address_in_turn(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.getaddrinfo
    port = int(server.url.rsplit(":", 1)[1])
    answers = {
        "both.example": [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)), *real("127.0.0.1", port)],
        "closed.example": [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))],
    }

    def lookup(host: str, *args: Any, **kwargs: Any) -> Any:
        if host == "missing.example":
            raise socket.gaierror(socket.EAI_NONAME, "not known")
        return answers.get(host) or real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(http, "refused", lambda address: "")
    server.answers = [(200, {}, b"picture")]
    assert http.request("GET", f"http://both.example:{port}/a", untrusted=True) == b"picture"
    with pytest.raises(http.RequestError, match="NewConnectionError"):
        http.request("GET", f"http://closed.example:{port}/a", untrusted=True, retries=1)
    with pytest.raises(http.RequestError, match="NameResolutionError"):
        http.request("GET", f"http://missing.example:{port}/a", untrusted=True, retries=1)
