import hashlib
import json
import urllib.parse
from collections.abc import Callable
from typing import Any

import pytest

from posteryard import http
from posteryard.plex import Plex
from posteryard.server import external_ids, labels, tmdb_id

BASE = "http://plex.example:32400"


class Server:
    """Answers Plex requests from a table of path -> JSON, and records every call."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, method: str, url: str, *, headers: dict[str, str], redirects: bool, **kwargs: Any) -> bytes:
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        assert headers["X-Plex-Token"] == "example-token"
        assert "example-token" not in url
        assert not redirects
        self.calls.append((method, parts.path, query))
        answer = self.routes.get(f"{method} {parts.path}", self.routes.get(parts.path))
        if callable(answer):
            answer = answer(query)
        if answer is None:
            raise http.HttpError(404, url)
        return answer if isinstance(answer, bytes) else json.dumps({"MediaContainer": answer}).encode()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], tuple[Plex, Server]]:
    def start(routes: dict[str, Any]) -> tuple[Plex, Server]:
        server = Server(routes)
        monkeypatch.setattr(http, "request", server)
        return Plex(BASE, "example-token"), server

    return start


def test_items_children_and_a_missing_item(serve: Any) -> None:
    plex, _ = serve(
        {
            "/library/sections": {"Directory": [{"key": "3", "title": "Movies"}]},
            "/library/metadata/1": {"Metadata": [{"ratingKey": "1"}]},
            "/library/metadata/1/children": {"Metadata": [{"ratingKey": "2"}]},
        }
    )
    assert plex.sections() == [{"key": "3", "title": "Movies"}]
    assert plex.item("1") == {"ratingKey": "1"}
    assert plex.item("9") is None
    assert plex.item("0123456789abcdef0123456789abcdef") is None
    assert plex.children("1") == [{"ratingKey": "2"}]


def test_section_items_reads_every_page(serve: Any) -> None:
    def page(query: dict[str, str]) -> dict[str, Any]:
        start = int(query["X-Plex-Container-Start"])
        keys = list(range(start, min(start + 200, 450)))
        return {"totalSize": 450, "Metadata": [{"ratingKey": str(k)} for k in keys]}

    plex, server = serve({"/library/sections/3/all": page})
    assert len(list(plex.section_items("3", "movie", label="x"))) == 450
    assert [c[2]["X-Plex-Container-Start"] for c in server.calls] == ["0", "200", "400"]
    assert server.calls[0][2]["label"] == "x"


def test_changed_since_merges_added_and_updated(serve: Any) -> None:
    def changed(query: dict[str, str]) -> dict[str, Any]:
        keys = ["1", "2"] if "addedAt>>" in query else ["2", "3"]
        return {"totalSize": 2, "Metadata": [{"ratingKey": k} for k in keys]}

    plex, _ = serve({"/library/sections/3/all": changed})
    assert sorted(i["ratingKey"] for i in plex.changed_since("3", "show", 100)) == ["1", "2", "3"]


def test_upload_selects_the_new_image(serve: Any) -> None:
    listings = iter([[{"ratingKey": "old", "selected": True}], [{"ratingKey": "old"}, {"ratingKey": "new"}]])
    plex, server = serve(
        {
            "GET /library/metadata/1/posters": lambda _q: {"Metadata": next(listings)},
            "POST /library/metadata/1/posters": b"",
            "PUT /library/metadata/1/poster": b"",
        }
    )
    assert plex.upload("1", "poster", b"jpeg") == "new"
    assert server.calls[-1] == ("PUT", "/library/metadata/1/poster", {"url": "new"})


def test_upload_of_bytes_uploaded_before_selects_their_image(serve: Any) -> None:
    earlier = "upload://posters/" + hashlib.sha1(b"jpeg").hexdigest()
    listing = [{"ratingKey": earlier}, {"ratingKey": "upload://posters/other", "selected": True}]
    plex, server = serve(
        {
            "GET /library/metadata/1/posters": {"Metadata": listing},
            "POST /library/metadata/1/posters": b"",
            "PUT /library/metadata/1/poster": b"",
        }
    )
    assert plex.upload("1", "poster", b"jpeg") == earlier
    assert server.calls[-1] == ("PUT", "/library/metadata/1/poster", {"url": earlier})


def test_upload_that_leaves_nothing_fails(serve: Any) -> None:
    plex, _ = serve({"GET /library/metadata/1/arts": {"Metadata": []}, "POST /library/metadata/1/arts": b""})
    with pytest.raises(http.RequestError, match="no art"):
        plex.upload("1", "art", b"jpeg")


def test_selected_poster_bytes_label_and_lock(serve: Any) -> None:
    plex, server = serve(
        {
            "/library/metadata/1/posters": {"Metadata": [{"ratingKey": "a"}, {"ratingKey": "b", "selected": True}]},
            "/library/metadata/1/thumb/5": b"image",
            "PUT /library/sections/3/all": b"",
        }
    )
    item = {"ratingKey": "1", "type": "movie", "librarySectionID": 3, "thumb": "/library/metadata/1/thumb/5"}
    assert plex.selected("1", "poster") == "b"
    assert plex.poster_bytes(item) == b"image"
    with pytest.raises(http.RequestError, match="no poster"):
        plex.poster_bytes({**item, "thumb": "https://elsewhere.example/a.jpg"})
    plex.remove_label(item, "posteryard-next")
    plex.lock(item, "art")
    assert server.calls[-2][2] == {"type": "1", "id": "1", "label[].tag.tag-": "posteryard-next"}
    assert server.calls[-1][2] == {"type": "1", "id": "1", "art.locked": "1"}


def test_guids_and_labels() -> None:
    assert tmdb_id({"Guid": [{"id": "imdb://tt1"}, {"id": "tmdb://42"}]}) == 42
    assert tmdb_id({}) is None
    assert tmdb_id({"guid": "com.plexapp.agents.themoviedb://603?lang=en"}) == 603
    assert tmdb_id({"Guid": [{"id": "tmdb://"}]}) is None
    assert external_ids({"guid": "com.plexapp.agents.thetvdb://121361/1/2?lang=en"}) == {"tvdb": "121361"}
    assert external_ids({"guid": "com.plexapp.agents.hama://anidb-1?lang=en"}) == {}
    assert external_ids({"Guid": [{"id": "imdb://tt1"}, {"id": "tvdb://5"}]}) == {"imdb": "tt1", "tvdb": "5"}
    assert labels({"Label": [{"tag": "Posteryard-Next"}]}) == {"posteryard-next"}


def test_restore_selects_the_agents_image_and_unlocks(serve: Any) -> None:
    images = [{"ratingKey": "upload://posters/1"}, {"ratingKey": "metadata://posters/agent"}]
    plex, server = serve(
        {
            "GET /library/metadata/7/posters": {"Metadata": images},
            "PUT /library/metadata/7/poster": b"",
            "PUT /library/sections/4/all": b"",
        }
    )
    plex.restore({"ratingKey": "7", "type": "episode", "librarySectionID": 4}, "thumb")
    puts = [call for call in server.calls if call[0] == "PUT"]
    assert puts[0][2]["thumb.locked"] == "0"
    assert puts[1] == ("PUT", "/library/metadata/7/poster", {"url": "metadata://posters/agent"})


def test_newest_added(serve: Any) -> None:
    plex, server = serve({"/library/sections/4/all": lambda q: {"Metadata": [{"addedAt": 1700000000}]}})
    assert plex.newest_added("4", "episode", **{"show.id": "9"}) == 1700000000
    assert server.calls[-1][2]["sort"] == "addedAt:desc"
    assert server.calls[-1][2]["show.id"] == "9"


def test_collection_calls(serve: Any) -> None:
    plex, server = serve(
        {
            "/identity": {"machineIdentifier": "abc"},
            "POST /library/collections": {"Metadata": [{"ratingKey": "60"}]},
            "PUT /library/collections/60/items": b"",
            "DELETE /library/collections/60/items/2": b"",
            "DELETE /library/collections/60": b"",
            "PUT /library/sections/4/all": b"",
            "/library/collections/60/children": {"Metadata": [{"ratingKey": "1"}]},
        }
    )
    assert plex.create_collection("4", "show", "Netflix", ["1", "2"]) == "60"
    created = server.calls[-1][2]
    assert created["uri"] == "server://abc/com.plexapp.plugins.library/library/metadata/1,2"
    assert (created["type"], created["sectionId"], created["title"]) == ("2", "4", "Netflix")
    plex.add_to_collection("60", ["3"])
    plex.remove_from_collection("60", "2")
    plex.set_label("4", "collection", "60", "posteryard-collection")
    assert server.calls[-1][2]["type"] == "18"
    assert [c["ratingKey"] for c in plex.collection_children("60")] == ["1"]
    plex.delete_collection("60")
    assert plex.server_id() == "plex:abc"
    assert [c[:2] for c in server.calls].count(("GET", "/identity")) == 1
