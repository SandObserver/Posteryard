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
        return answers["entity"]

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
