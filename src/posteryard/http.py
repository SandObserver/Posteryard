import contextlib
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.parse
import zlib
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any

import urllib3

from posteryard import __version__

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SECRET_PARAMS = frozenset(
    {"x-plex-token", "api_key", "token", "apikey", "username", "user", "password", "pass", "passwd", "pwd", "key"}
)
NAMES = frozenset({"username", "user"})
SECRET_HEADERS = frozenset({"x-plex-token", "authorization", "api-key"})
CROSS_HOST_DROPPED = SECRET_HEADERS | {"cookie", "proxy-authorization"}
USER_AGENT = f"Posteryard/{__version__} (+https://github.com/SandObserver/Posteryard)"
MAX_RESPONSE = 64 * 1024 * 1024
ERROR_EXCERPT = 4096
MAX_REDIRECTS = 5
DEFAULT_PORTS = {"http": 80, "https": 443}
RETRY_AFTER_MAX = 60.0
DEADLINE_FACTOR = 4
READ_CHUNK = 256 * 1024
GZIP = frozenset({"gzip", "x-gzip"})
METADATA = frozenset({ipaddress.ip_address("100.100.100.200"), ipaddress.ip_address("fd00:ec2::254")})


class RefusedError(ValueError):
    pass


def refused(address: str) -> str:
    """Why an address from guide content may not be fetched, or an empty string. LAN addresses are allowed."""
    ip = ipaddress.ip_address(address)
    checks = {
        "loopback": ip.is_loopback,
        "link-local": ip.is_link_local,
        "multicast": ip.is_multicast,
        "reserved": ip.is_reserved,
        "unspecified": ip.is_unspecified,
        "metadata service": (getattr(ip, "ipv4_mapped", None) or ip) in METADATA,
    }
    return next((f"a {name} address" for name, hit in checks.items() if hit), "")


_calls = threading.local()


def _hang_up(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


class _Deadline:
    """Hangs up the connection in use when the time is up. A server can send one byte per read timeout forever."""

    def __init__(self, seconds: float) -> None:
        self.ends = time.monotonic() + seconds
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._expired = False
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def left(self) -> float:
        return self.ends - time.monotonic()

    @property
    def expired(self) -> bool:
        return self._expired or self.left() <= 0

    def watch(self, sock: socket.socket | None) -> None:
        with self._lock:
            self._sock = sock
            if self._expired and sock is not None:
                _hang_up(sock)

    def cancel(self) -> None:
        self._timer.cancel()
        with self._lock:
            self._sock = None

    def _expire(self) -> None:
        with self._lock:
            self._expired = True
            if self._sock is not None:
                _hang_up(self._sock)


class _Connection(urllib3.connection.HTTPConnection):
    def connect(self) -> None:
        super().connect()
        _watch(self.sock)

    def getresponse(self) -> urllib3.response.HTTPResponse:  # type: ignore[override]
        _watch(self.sock)
        return super().getresponse()

    def _new_conn(self) -> socket.socket:
        """Resolve once, then try each address in the time left. A host can list many addresses that never answer."""
        name, timeout = self._dns_host, self.timeout
        try:
            found = socket.getaddrinfo(
                name, self.port, urllib3.util.connection.allowed_gai_family(), socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise urllib3.exceptions.NameResolutionError(self.host, self, exc) from exc
        addresses = self._checked(list(dict.fromkeys(str(info[4][0]) for info in found)))
        deadline: _Deadline | None = getattr(_calls, "deadline", None)
        failure = urllib3.exceptions.ConnectTimeoutError(self, f"connection to {name} timed out")
        try:
            for address in addresses:
                left = deadline.left() if deadline else float("inf")
                if left <= 0:
                    break
                self._dns_host = address
                self.timeout = min(timeout, left) if isinstance(timeout, (int, float)) else left
                try:
                    return super()._new_conn()
                except urllib3.exceptions.ConnectTimeoutError as exc:
                    failure = exc
        finally:
            self._dns_host, self.timeout = name, timeout
        raise failure

    def _checked(self, addresses: list[str]) -> list[str]:
        return addresses


def _watch(sock: socket.socket | None) -> None:
    if (deadline := getattr(_calls, "deadline", None)) is not None:
        deadline.watch(sock)


class _HTTPSConnection(_Connection, urllib3.connection.HTTPSConnection):
    pass


class _Pool(urllib3.HTTPConnectionPool):
    ConnectionCls = _Connection


class _HTTPSPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = _HTTPSConnection


class _UntrustedConnection(_Connection):
    def _checked(self, addresses: list[str]) -> list[str]:
        """Connect only to the checked addresses. A second lookup could answer differently."""
        for address in addresses:
            if reason := refused(address):
                raise RefusedError(f"refused {self._dns_host} as {reason}")
        return addresses


class _UntrustedHTTPSConnection(_UntrustedConnection, _HTTPSConnection):
    pass


class _UntrustedPool(urllib3.HTTPConnectionPool):
    ConnectionCls = _UntrustedConnection


class _UntrustedHTTPSPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = _UntrustedHTTPSConnection


def _pool_manager(
    http_pool: type[urllib3.HTTPConnectionPool], https_pool: type[urllib3.HTTPConnectionPool]
) -> urllib3.PoolManager:
    manager = urllib3.PoolManager(
        num_pools=16,
        maxsize=4,
        retries=urllib3.Retry(
            total=None, connect=0, read=0, status=0, other=0, redirect=0, raise_on_redirect=False, raise_on_status=False
        ),
    )
    manager.pool_classes_by_scheme = {"http": http_pool, "https": https_pool}
    return manager


POOL = _pool_manager(_Pool, _HTTPSPool)
UNTRUSTED_POOL = _pool_manager(_UntrustedPool, _UntrustedHTTPSPool)


class RequestError(Exception):
    """Raised for every failed request. The message never contains a credential."""

    def __init__(self, message: str, url: str) -> None:
        super().__init__(f"{message} for {redact(url)}")
        self.url = redact(url)


class HttpError(RequestError):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        detail = "" if body.lstrip().startswith("<") else " ".join(body.split())[:200]
        super().__init__(f"HTTP {status}: {detail}" if detail else f"HTTP {status}", url)
        self.status = status


class RedirectError(RequestError):
    def __init__(self, status: int, url: str, location: str) -> None:
        super().__init__(f"refused a redirect (HTTP {status})", url)
        self.location = _target(url, location)


def gone(exc: RequestError) -> bool:
    return isinstance(exc, HttpError) and 400 <= exc.status < 500 and exc.status not in RETRY_STATUSES


def redact(url: str) -> str:
    """Pass every URL through this before it reaches a log or an error. It hides credentials."""
    parts = urllib.parse.urlsplit(url)
    query = [
        (k, "***" if k.lower() in SECRET_PARAMS else v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ]
    netloc = "***@" + parts.netloc.rpartition("@")[2] if "@" in parts.netloc else parts.netloc
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc, query=urllib.parse.urlencode(query)))


def secrets(url: str, headers: dict[str, str]) -> set[str]:
    parts = urllib.parse.urlsplit(url)
    found = {v for k, v in urllib.parse.parse_qsl(parts.query) if k.lower() in SECRET_PARAMS - NAMES}
    found.add(parts.password or "")
    for name, value in headers.items():
        if name.lower() in SECRET_HEADERS:
            last = (value.split() or [value])[-1]
            found |= {value, last, last.removeprefix("Token=").strip('"')}
    return {s for s in found if s}


def hide(text: str, hidden: set[str]) -> str:
    """Pass every response body through this before it reaches an error. A server can echo a credential back."""
    for secret in sorted(hidden, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def request(  # noqa: PLR0913
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30,
    retries: int = 3,
    redirects: bool = True,
    untrusted: bool = False,
) -> bytes:
    """Set `untrusted` for an address taken from guide content. Every redirect is then checked too.

    `timeout` limits each wait for the server. The whole call, retries and redirects included, stops after
    DEADLINE_FACTOR times it.
    """
    hop = _Hop(method, url, headers or {}, data, untrusted)
    return _call(hop, timeout, retries, redirects=redirects, check_redirects=untrusted)


def get_checked_redirects(url: str, *, timeout: float) -> bytes:
    """For a trusted address on a host the operator does not run. A redirect from it is checked as guide content."""
    return _call(_Hop("GET", url, {}, None, untrusted=False), timeout, 3, redirects=True, check_redirects=True)


@dataclass(frozen=True)
class _Hop:
    method: str
    url: str
    headers: dict[str, str]
    data: bytes | None
    untrusted: bool


def _call(hop: _Hop, timeout: float, retries: int, *, redirects: bool, check_redirects: bool) -> bytes:
    """Do not let urllib3 follow redirects. It reads a redirect's body with no time or size limit."""
    deadline = _Deadline(timeout * DEADLINE_FACTOR)
    _calls.deadline = deadline
    try:
        for _ in range(MAX_REDIRECTS + 1):
            status, body, location = _attempts(hop, timeout, retries, deadline)
            if not 300 <= status < 400:
                return body
            target = _target(hop.url, location)
            origin = _origin(target)
            if not redirects or origin is None or origin[0] not in ("http", "https"):
                raise RedirectError(status, hop.url, location)
            headers = hop.headers
            if origin != _origin(hop.url):
                headers = {k: v for k, v in headers.items() if k.lower() not in CROSS_HOST_DROPPED}
            method, data = ("GET", None) if status == HTTPStatus.SEE_OTHER else (hop.method, hop.data)
            hop = _Hop(method, target, headers, data, hop.untrusted or check_redirects)
        raise RequestError("too many redirects", hop.url)
    finally:
        deadline.cancel()
        _calls.deadline = None


def _target(base: str, location: str) -> str:
    try:
        return urllib.parse.urljoin(base, location) if location else ""
    except ValueError:
        return ""


def _origin(url: str) -> tuple[str, str, int] | None:
    """The scheme, host and port urllib3 connects to, or None when Python reads the address differently.

    A redirect to an address the two read differently is refused. Its credentials could reach another host.
    """
    try:
        ours, theirs = urllib.parse.urlsplit(url), urllib3.util.parse_url(url)
        host = ours.hostname or ""
        python_host = urllib3.util.parse_url(f"//[{host}]" if ":" in host else f"//{host}").host or ""
        scheme = (theirs.scheme or "").lower()
        origin = (scheme, (theirs.host or "").lower(), theirs.port or DEFAULT_PORTS.get(scheme, 0))
        python = ((ours.scheme or "").lower(), python_host.lower(), ours.port or DEFAULT_PORTS.get(scheme, 0))
    except ValueError:
        return None
    return origin if origin == python else None


def _attempts(hop: _Hop, timeout: float, retries: int, deadline: _Deadline) -> tuple[int, bytes, str]:
    delay = 2.0
    for attempt in range(1, retries + 1):
        wait = delay
        try:
            left = max(deadline.left(), 0.001)
            response = (UNTRUSTED_POOL if hop.untrusted else POOL).request(
                hop.method,
                hop.url,
                body=hop.data,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **hop.headers},
                timeout=urllib3.Timeout(connect=min(timeout, left), read=min(timeout, left)),
                preload_content=False,
                decode_content=False,
                redirect=False,
            )
            status, body, location = _answer(response, hop, deadline)
            if status < 400:
                return status, body, location
            wait = max(delay, retry_after(response.headers.get("Retry-After")))
            if status not in RETRY_STATUSES or attempt == retries or wait >= deadline.left():
                raise HttpError(status, hop.url, _error_text(body, hop))
            log.warning(
                "retrying request", extra={"method": hop.method, "url": redact(hop.url), "reason": f"HTTP {status}"}
            )
        except urllib3.exceptions.LocationValueError as exc:
            raise RequestError(type(exc).__name__, hop.url) from None
        except urllib3.exceptions.HTTPError as exc:
            if deadline.expired:
                raise RequestError("response too slow", hop.url) from None
            last = attempt == retries or delay >= deadline.left()
            if _network_failure(exc, hop.method, hop.url, last=last, first=attempt == 1):
                continue
        except ValueError as exc:
            reason = str(exc) if isinstance(exc, RefusedError) else type(exc).__name__
            raise RequestError(reason, hop.url) from None
        time.sleep(wait)
        delay *= 2
    raise AssertionError("unreachable")


def _answer(response: urllib3.BaseHTTPResponse, hop: _Hop, deadline: _Deadline) -> tuple[int, bytes, str]:
    try:
        if 300 <= response.status < 400:
            response.close()
            return response.status, b"", response.headers.get("Location") or ""
        if response.status < 400:
            return response.status, _read_body(response, hop.url, deadline, MAX_RESPONSE), ""
        excerpt = _read_body(response, hop.url, deadline, ERROR_EXCERPT, cut=True)
        if len(excerpt) >= ERROR_EXCERPT:
            response.close()
        return response.status, excerpt, ""
    except RequestError:
        response.close()
        raise
    finally:
        response.release_conn()


def _error_text(excerpt: bytes, hop: _Hop) -> str:
    """An excerpt that was cut loses its last word. A credential cut in two must not show its first half."""
    text = hide(excerpt.decode("utf-8", "replace"), secrets(hop.url, hop.headers))
    if len(excerpt) >= ERROR_EXCERPT:
        end = max(text.rfind(space) for space in " \t\r\n")
        text = text[:end] if end > 0 else ""
    return text


class _Gunzip:
    """Never return more than `limit` bytes. Data after a whole member that is not gzip is ignored."""

    def __init__(self) -> None:
        self._obj: zlib._Decompress | None = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._first = True

    @property
    def complete(self) -> bool:
        return self._obj is None or self._obj.eof

    def feed(self, data: bytes, limit: int) -> bytes:
        out = bytearray()
        while data and self._obj and len(out) < limit:
            if self._obj.eof:
                self._obj, self._first = zlib.decompressobj(16 + zlib.MAX_WBITS), False
            try:
                out += self._obj.decompress(data, limit - len(out))
            except zlib.error:
                if self._first:
                    raise
                self._obj = None
                break
            data = self._obj.unconsumed_tail or self._obj.unused_data
        return bytes(out)


def _read_body(
    response: urllib3.BaseHTTPResponse, url: str, deadline: _Deadline, limit: int, *, cut: bool = False
) -> bytes:
    """Check both limits after every network chunk. A chunk can unpack to nothing.

    `cut` keeps the first `limit` bytes of a longer body in place of refusing it.
    """
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in GZIP | {"", "identity"}:
        raise RequestError(f"unsupported compression {encoding[:20]!r}", url)
    gunzip = _Gunzip() if encoding in GZIP else None
    chunks: list[bytes] = []
    raw = size = 0
    while data := response.read1(READ_CHUNK, decode_content=False):
        raw += len(data)
        if gunzip:
            try:
                data = gunzip.feed(data, limit + 1 - size)
            except zlib.error:
                raise RequestError("broken compressed response", url) from None
        chunks.append(data)
        size += len(data)
        if cut and max(raw, size) >= limit:
            return b"".join(chunks)[:limit]
        if max(raw, size) > limit:
            raise RequestError(f"response larger than {limit // 1024 // 1024} MB", url)
        if deadline.expired:
            raise RequestError("response too slow", url)
    # A hang-up at the deadline ends a body without a length like its real end.
    if deadline.expired:
        raise RequestError("response too slow", url)
    if gunzip and raw and not gunzip.complete:
        raise RequestError("compressed response cut off", url)
    return b"".join(chunks)


def _network_failure(exc: urllib3.exceptions.HTTPError, method: str, url: str, *, last: bool, first: bool) -> bool:
    """True to try again at once: a pooled connection the other side dropped while idle."""
    cause = getattr(exc, "reason", None) or exc
    if last:
        raise RequestError(type(cause).__name__, url) from None
    if first and isinstance(cause, urllib3.exceptions.ProtocolError):
        log.debug("connection dropped, trying again", extra={"method": method, "url": redact(url)})
        return True
    log.warning("retrying request", extra={"method": method, "url": redact(url), "reason": type(cause).__name__})
    return False


def retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return 0.0
    return min(max(seconds, 0.0), RETRY_AFTER_MAX)


def get_json(url: str, headers: dict[str, str] | None = None, *, redirects: bool = True) -> Any:
    return json.loads(
        request("GET", url, headers={"Accept": "application/json", **(headers or {})}, redirects=redirects)
    )
