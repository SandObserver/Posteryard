"""Small HTTP client with timeouts and retries on transient failures."""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SECRET_PARAMS = frozenset({"x-plex-token", "api_key", "token", "apikey"})
USER_AGENT = "Posteryard"


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} for {redact(url)}: {body[:200]}")
        self.status = status
        self.url = redact(url)


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
        req = urllib.request.Request(
            url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body: bytes = resp.read()
                return body
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            if exc.code not in RETRY_STATUSES or attempt == retries:
                raise HttpError(exc.code, url, text) from None
            log.warning("retrying %s %s after HTTP %s", method, redact(url), exc.code)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise
            log.warning("retrying %s %s after %s", method, redact(url), exc)
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    return json.loads(request("GET", url, headers={"Accept": "application/json", **(headers or {})}, timeout=timeout))
