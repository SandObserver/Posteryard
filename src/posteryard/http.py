import ipaddress
import json
import logging
import socket
import time
import urllib.parse
import zlib
from email.utils import parsedate_to_datetime
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
USER_AGENT = f"Posteryard/{__version__} (+https://github.com/SandObserver/Posteryard)"
MAX_RESPONSE = 64 * 1024 * 1024
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


class _UntrustedConnection(urllib3.connection.HTTPConnection):
    def _new_conn(self) -> socket.socket:
        """Resolve once and connect to the checked address. A second lookup could answer differently."""
        name = self._dns_host
        try:
            found = socket.getaddrinfo(name, self.port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise urllib3.exceptions.NameResolutionError(self.host, self, exc) from exc
        addresses = list(dict.fromkeys(str(info[4][0]) for info in found))
        for address in addresses:
            if reason := refused(address):
                raise RefusedError(f"refused {name} as {reason}")
        failure: urllib3.exceptions.ConnectTimeoutError | None = None
        try:
            for address in addresses:
                self._dns_host = address
                try:
                    return super()._new_conn()
                except urllib3.exceptions.ConnectTimeoutError as exc:
                    failure = exc
        finally:
            self._dns_host = name
        assert failure is not None
        raise failure


class _UntrustedHTTPSConnection(_UntrustedConnection, urllib3.connection.HTTPSConnection):
    pass


class _UntrustedPool(urllib3.HTTPConnectionPool):
    ConnectionCls = _UntrustedConnection


class _UntrustedHTTPSPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = _UntrustedHTTPSConnection


def _pool_manager() -> urllib3.PoolManager:
    return urllib3.PoolManager(
        num_pools=16,
        maxsize=4,
        retries=urllib3.Retry(
            total=None,
            connect=0,
            read=0,
            status=0,
            other=0,
            redirect=5,
            raise_on_status=False,
            remove_headers_on_redirect=urllib3.Retry.DEFAULT_REMOVE_HEADERS_ON_REDIRECT | {"api-key"},
        ),
    )


POOL = _pool_manager()
UNTRUSTED_POOL = _pool_manager()
UNTRUSTED_POOL.pool_classes_by_scheme = {"http": _UntrustedPool, "https": _UntrustedHTTPSPool}


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
        self.location = urllib.parse.urljoin(url, location) if location else ""


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
    """Set `untrusted` for an address taken from guide content. Every redirect is then checked too."""
    delay = 2.0
    for attempt in range(1, retries + 1):
        wait = delay
        deadline = time.monotonic() + timeout * DEADLINE_FACTOR
        try:
            response = (UNTRUSTED_POOL if untrusted else POOL).request(
                method,
                url,
                body=data,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})},
                timeout=urllib3.Timeout(connect=timeout, read=timeout),
                preload_content=False,
                decode_content=False,
                redirect=redirects,
            )
            try:
                body = _read_body(response, url, deadline)
            except RequestError:
                response.close()
                raise
            finally:
                response.release_conn()
            if not redirects and 300 <= response.status < 400:
                raise RedirectError(response.status, url, response.headers.get("Location") or "")
            if response.status < 400:
                return body
            if response.status not in RETRY_STATUSES or attempt == retries:
                text = body.decode("utf-8", "replace")
                raise HttpError(response.status, url, hide(text, secrets(url, headers or {})))
            wait = max(delay, retry_after(response.headers.get("Retry-After")))
            log.warning(
                "retrying request", extra={"method": method, "url": redact(url), "reason": f"HTTP {response.status}"}
            )
        except urllib3.exceptions.LocationValueError as exc:
            raise RequestError(type(exc).__name__, url) from None
        except urllib3.exceptions.HTTPError as exc:
            if _network_failure(exc, method, url, last=attempt == retries, first=attempt == 1):
                continue
        except ValueError as exc:
            raise RequestError(str(exc) if isinstance(exc, RefusedError) else type(exc).__name__, url) from None
        time.sleep(wait)
        delay *= 2
    raise AssertionError("unreachable")


def get_checked_redirects(url: str, *, timeout: float) -> bytes:
    """For a trusted address on a host the operator does not run. A redirect from it is checked as guide content."""
    try:
        return request("GET", url, timeout=timeout, redirects=False)
    except RedirectError as exc:
        if not exc.location:
            raise
        return request("GET", exc.location, timeout=timeout, untrusted=True)


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


def _read_body(response: urllib3.BaseHTTPResponse, url: str, deadline: float) -> bytes:
    """The read timeout only limits the wait for each chunk, not the whole body.

    Check both limits after every network chunk. A chunk can unpack to nothing.
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
                data = gunzip.feed(data, MAX_RESPONSE + 1 - size)
            except zlib.error:
                raise RequestError("broken compressed response", url) from None
        chunks.append(data)
        size += len(data)
        if max(raw, size) > MAX_RESPONSE:
            raise RequestError("response larger than 64 MB", url)
        if time.monotonic() > deadline:
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
