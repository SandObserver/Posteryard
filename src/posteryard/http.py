import json
import logging
import time
import urllib.parse
from email.utils import parsedate_to_datetime
from typing import Any

import urllib3

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SECRET_PARAMS = frozenset({"x-plex-token", "api_key", "token", "apikey"})
USER_AGENT = "Posteryard"
MAX_RESPONSE = 64 * 1024 * 1024
RETRY_AFTER_MAX = 60.0
# Keeps connections to Plex, Jellyfin and TMDB open between requests. Redirects are followed; retries are below.
POOL = urllib3.PoolManager(
    num_pools=16,
    maxsize=4,
    retries=urllib3.Retry(total=None, connect=0, read=0, status=0, other=0, redirect=5, raise_on_status=False),
)


class RequestError(Exception):
    """Raised for every failed request. The message never contains a credential."""

    def __init__(self, message: str, url: str) -> None:
        super().__init__(f"{message} for {redact(url)}")
        self.url = redact(url)


class HttpError(RequestError):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status}: {body[:200]}", url)
        self.status = status


def redact(url: str) -> str:
    """Pass every URL through this before it reaches a log or an error. It hides credentials."""
    parts = urllib.parse.urlsplit(url)
    query = [
        (k, "***" if k.lower() in SECRET_PARAMS else v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30,
    retries: int = 3,
) -> bytes:
    delay = 2.0
    for attempt in range(1, retries + 1):
        wait = delay
        try:
            response = POOL.request(
                method,
                url,
                body=data,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                timeout=urllib3.Timeout(connect=timeout, read=timeout),
                preload_content=False,
            )
            try:
                body: bytes = response.read(MAX_RESPONSE + 1)
            finally:
                response.release_conn()
            if len(body) > MAX_RESPONSE:
                raise RequestError("response larger than 64 MB", url)
            if response.status < 400:
                return body
            if response.status not in RETRY_STATUSES or attempt == retries:
                raise HttpError(response.status, url, body.decode("utf-8", "replace"))
            wait = max(delay, retry_after(response.headers.get("Retry-After")))
            log.warning("retrying %s %s after HTTP %s", method, redact(url), response.status)
        except urllib3.exceptions.LocationValueError as exc:
            raise RequestError(type(exc).__name__, url) from None
        except urllib3.exceptions.HTTPError as exc:
            reason = type(getattr(exc, "reason", None) or exc).__name__
            if attempt == retries:
                raise RequestError(reason, url) from None
            log.warning("retrying %s %s after %s", method, redact(url), reason)
        time.sleep(wait)
        delay *= 2
    raise AssertionError("unreachable")


def retry_after(value: str | None) -> float:
    """Seconds a server asks to wait, as a number or an HTTP date, at most RETRY_AFTER_MAX."""
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


def get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    return json.loads(request("GET", url, headers={"Accept": "application/json", **(headers or {})}))
