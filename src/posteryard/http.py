import http.client
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
MAX_RESPONSE = 64 * 1024 * 1024


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
        try:
            req = urllib.request.Request(
                url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body: bytes = resp.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise RequestError("response larger than 64 MB", url)
            return body
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            if exc.code not in RETRY_STATUSES or attempt == retries:
                raise HttpError(exc.code, url, text) from None
            log.warning("retrying %s %s after HTTP %s", method, redact(url), exc.code)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise RequestError(type(exc).__name__, url) from None
            log.warning("retrying %s %s after %s", method, redact(url), type(exc).__name__)
        except (ValueError, http.client.HTTPException) as exc:
            raise RequestError(type(exc).__name__, url) from None
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


def get_json(url: str) -> Any:
    return json.loads(request("GET", url, headers={"Accept": "application/json"}))
