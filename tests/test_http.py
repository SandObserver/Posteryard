import io
import urllib.error
import urllib.request
from email.message import Message

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
    for key in ("1 2", "١٢", "../1"):
        with pytest.raises(ValueError, match="rating key"):
            Plex("http://plex.example:32400", SECRET).item(key)


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


def test_transient_failures_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[Exception | bytes] = [
        urllib.error.HTTPError("http://x.example", 503, "busy", Message(), io.BytesIO(b"busy")),
        urllib.error.URLError("refused"),
        b"ok",
    ]
    sleeps: list[float] = []

    def urlopen(request: object, timeout: float) -> Response:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("posteryard.http.time.sleep", sleeps.append)
    assert http.request("GET", f"http://x.example/a?api_key={SECRET}") == b"ok"
    assert sleeps == [2.0, 4.0]


def test_client_errors_are_not_retried_and_hide_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: object, timeout: float) -> Response:
        raise urllib.error.HTTPError("http://x.example", 401, "no", Message(), io.BytesIO(b"denied"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(http.HttpError) as caught:
        http.request("GET", f"http://x.example/a?X-Plex-Token={SECRET}")
    assert caught.value.status == 401
    assert SECRET not in str(caught.value)


def test_oversized_responses_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "MAX_RESPONSE", 4)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Response(b"too long"))
    with pytest.raises(http.RequestError, match="larger than"):
        http.request("GET", "http://x.example/a")
