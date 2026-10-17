import json
from typing import Any

import pytest

from posteryard import apple, http
from posteryard.tmdb import Details

UMC = "umc.cmc.hrenotb7pqaz2mo61vph1gwj"
TEMPLATE = "https://is1-ssl.mzstatic.com/image/thumb/Features/v4/ab/cd/art.jpg/{w}x{h}nr.{f}"
ENTITY = {"entities": {"Q147235": {"claims": {"P9751": [{"mainsnak": {"datavalue": {"value": UMC}}}]}}}}
PAGE = (
    '<html><script type="application/json" id="serialized-server-data">'
    + json.dumps([{"data": {"artwork": {"tall": {"template": TEMPLATE, "width": 1680}}}}])
    + "</script></html>"
)


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    answers: dict[str, Any] = {"entity": ENTITY, "page": PAGE.encode()}

    def get_json(url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        assert url == "https://www.wikidata.org/wiki/Special:EntityData/Q147235.json"
        entity = answers["entity"]
        if isinstance(entity, Exception):
            raise entity
        return entity

    def request(method: str, url: str, **kwargs: Any) -> bytes:
        assert url == f"https://tv.apple.com/ca/show/title/{UMC}"
        page = answers["page"]
        if isinstance(page, Exception):
            raise page
        return bytes(page)

    monkeypatch.setattr(http, "get_json", get_json)
    monkeypatch.setattr(http, "request", request)
    return answers


DETAILS: Details = {"external_ids": {"wikidata_id": "Q147235"}}


def test_tall_art_is_found_through_wikidata(web: dict[str, Any]) -> None:
    url = apple.find("tv", DETAILS, "CA")
    assert url == "https://is1-ssl.mzstatic.com/image/thumb/Features/v4/ab/cd/art.jpg/1680x3636nr.jpg"
    assert apple.is_apple(url)


def test_missing_ids_pages_and_unreadable_pages_give_none(web: dict[str, Any]) -> None:
    assert apple.find("tv", {"external_ids": {}}, "CA") is None
    assert apple.find("movie", DETAILS, "CA") is None
    web["page"] = b"<html>changed</html>"
    assert apple.find("tv", DETAILS, "CA") is None
    web["page"] = http.HttpError(404, "https://tv.apple.com/ca/show/title/x")
    assert apple.find("tv", DETAILS, "CA") is None
    web["page"] = http.RequestError("ConnectTimeoutError", "https://tv.apple.com/ca/show/title/x")
    with pytest.raises(http.RequestError):
        apple.find("tv", DETAILS, "CA")


def test_only_apple_image_hosts_count() -> None:
    assert not apple.is_apple("https://elsewhere.example/image/thumb/a.jpg")
    assert not apple.is_apple("/tmdb-path.jpg")


def test_a_missing_wikidata_entry_gives_none_and_other_errors_raise(web: dict[str, Any]) -> None:
    web["entity"] = http.HttpError(404, "https://www.wikidata.org/wiki/Special:EntityData/Q147235.json")
    assert apple.find("tv", DETAILS, "CA") is None
    web["entity"] = http.HttpError(503, "https://www.wikidata.org/wiki/Special:EntityData/Q147235.json")
    with pytest.raises(http.HttpError):
        apple.find("tv", DETAILS, "CA")


def test_a_claim_that_is_not_an_apple_id_is_skipped(web: dict[str, Any]) -> None:
    claims = [{"mainsnak": {"datavalue": {"value": "not-an-id"}}}, {"mainsnak": {"datavalue": {"value": UMC}}}]
    web["entity"] = {"entities": {"Q147235": {"claims": {"P9751": claims}}}}
    assert apple.find("tv", DETAILS, "CA") is not None


def test_a_page_error_other_than_404_raises(web: dict[str, Any]) -> None:
    web["page"] = http.HttpError(503, "https://tv.apple.com/ca/show/title/x")
    with pytest.raises(http.HttpError):
        apple.find("tv", DETAILS, "CA")


def test_a_page_with_broken_data_gives_none_and_a_warning(
    web: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    web["page"] = b'<script id="serialized-server-data">{broken</script>'
    assert apple.find("tv", DETAILS, "CA") is None
    assert "Apple TV page not readable" in caplog.text
