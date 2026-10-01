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
    with pytest.raises(ValueError, match="rating key"):
        Plex("http://plex.example:32400", SECRET).item("1 2")
