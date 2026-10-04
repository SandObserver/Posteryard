import base64
import json
import urllib.parse
from collections.abc import Callable
from typing import Any

import pytest

from posteryard import http
from posteryard.jellyfin import Jellyfin
from posteryard.quality import AudioLevel, Badge, HdrLevel, VideoLevel, accessibility, best

BASE = "http://jellyfin.example:8096"
MOVIE = "a" * 32
SERIES = "b" * 32
SEASON = "c" * 32
EPISODE = "d" * 32
LIBRARY = "e" * 32
USER = "f" * 32

FORD = {
    "Id": MOVIE,
    "Type": "Movie",
    "Name": "Ford v Ferrari",
    "Path": "/media/movies/Ford v Ferrari (2019)/Ford v Ferrari (2019).mkv",
    "DateCreated": "2026-10-03T22:05:11.4294499Z",
    "Tags": ["racing", "Posteryard-Ignore"],
    "ProviderIds": {"Tmdb": "359724", "Imdb": "tt7286456"},
    "ImageTags": {"Primary": "p1"},
    "BackdropImageTags": ["b1", "b2"],
    "MediaStreams": [
        {"Type": "Video", "Width": 3840, "Height": 1606, "VideoRangeType": "DOVIWithHDR10", "DisplayTitle": "4K HEVC"},
        {"Type": "Audio", "Channels": 8, "Profile": "Dolby TrueHD + Dolby Atmos", "DisplayTitle": "TrueHD 7.1"},
        {"Type": "Subtitle", "Codec": "subrip", "IsHearingImpaired": True, "DisplayTitle": "English - SDH"},
    ],
}
EPISODE_RAW = {
    "Id": EPISODE,
    "Type": "Episode",
    "Name": "Pilot",
    "IndexNumber": 1,
    "ParentIndexNumber": 2,
    "SeasonId": SEASON,
    "SeriesId": SERIES,
    "Path": "/media/tv/Show/Season 02/Show - S02E01.mkv",
}


class Server:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> bytes:
        parts = urllib.parse.urlsplit(url)
        assert kwargs["headers"]["Authorization"] == 'MediaBrowser Token="example-key"'
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((method, parts.path, query, kwargs.get("data")))
        answer = self.routes.get(f"{method} {parts.path}", self.routes.get(parts.path))
        if callable(answer):
            answer = answer(query)
        if answer is None:
            raise http.HttpError(404, url)
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], tuple[Jellyfin, Server]]:
    def start(routes: dict[str, Any]) -> tuple[Jellyfin, Server]:
        base = {
            "/Users": [{"Id": "0" * 32, "Policy": {}}, {"Id": USER, "Policy": {"IsAdministrator": True}}],
            "/Library/VirtualFolders": [
                {"ItemId": LIBRARY, "Name": "Movies", "CollectionType": "movies", "Locations": ["/media/movies"]},
                {"ItemId": "1" * 32, "Name": "Shows", "CollectionType": "tvshows", "Locations": ["/media/tv/"]},
                {"ItemId": "2" * 32, "Name": "Music", "CollectionType": "music", "Locations": ["/media/music"]},
            ],
        }
        server = Server({**base, **routes})
        monkeypatch.setattr(http, "request", server)
        return Jellyfin(BASE, "example-key"), server

    return start


def test_items_come_back_in_plex_shape(serve: Any) -> None:
    jellyfin, server = serve({f"/Items/{MOVIE}": FORD, f"/Items/{EPISODE}": EPISODE_RAW})
    movie = jellyfin.item(MOVIE)
    assert movie is not None
    assert (movie["ratingKey"], movie["type"], movie["title"]) == (MOVIE, "movie", "Ford v Ferrari")
    assert movie["Guid"] == [{"id": "tmdb://359724"}, {"id": "imdb://tt7286456"}]
    assert movie["librarySectionTitle"] == "Movies"
    assert movie["addedAt"] == 1791065111
    assert {label["tag"] for label in movie["Label"]} == {"racing", "Posteryard-Ignore"}
    assert next(c for c in server.calls if c[1] == f"/Items/{MOVIE}")[2] == {"userId": USER}
    quality = best(movie["Media"])
    assert (quality.video, quality.hdr, quality.audio) == (VideoLevel.UHD, HdrLevel.DOLBY_VISION, AudioLevel.ATMOS)
    episode = jellyfin.item(EPISODE)
    assert episode is not None
    assert (episode["parentRatingKey"], episode["grandparentRatingKey"]) == (SEASON, SERIES)
    assert (episode["index"], episode["parentIndex"], episode["librarySectionTitle"]) == (1, 2, "Shows")
    assert [s["title"] for s in jellyfin.sections()] == ["Movies", "Shows"]


def test_a_missing_item_is_none_and_bad_ids_are_refused(serve: Any) -> None:
    jellyfin, _ = serve({})
    assert jellyfin.item("9" * 32) is None
    assert jellyfin.item("../Users") is None
    assert jellyfin.item("1917") is None
    with pytest.raises(ValueError, match="not a Jellyfin item id"):
        jellyfin.children("../Users")


def test_section_items_filters_and_pages(serve: Any) -> None:
    pages = iter([{"Items": [FORD], "TotalRecordCount": 2}, {"Items": [FORD], "TotalRecordCount": 2}])
    jellyfin, server = serve({"/Items": lambda q: next(pages)})
    found = list(jellyfin.section_items(LIBRARY, "movie", label="posteryard-next", **{"updatedAt>>": 1791065111}))
    assert len(found) == 2
    item_calls = [c for c in server.calls if c[1] == "/Items"]
    query = item_calls[0][2]
    assert query["ParentId"] == LIBRARY
    assert query["IncludeItemTypes"] == "Movie"
    assert query["Tags"] == "posteryard-next"
    assert query["MinDateLastSaved"] == "2026-10-03T22:05:11Z"
    assert "MinDateCreated" not in query
    assert item_calls[1][2]["StartIndex"] == "1"


def test_collections_are_listed_without_a_library(serve: Any) -> None:
    jellyfin, server = serve({"/Items": {"Items": [], "TotalRecordCount": 0}})
    assert list(jellyfin.section_items(LIBRARY, "collection")) == []
    assert "ParentId" not in server.calls[-1][2]
    assert server.calls[-1][2]["IncludeItemTypes"] == "BoxSet"


def test_newest_added(serve: Any) -> None:
    jellyfin, server = serve({"/Items": {"Items": [{"DateCreated": "2026-10-03T22:05:11Z"}]}})
    assert jellyfin.newest_added(LIBRARY, "episode", **{"show.id": SERIES}) == 1791065111
    assert server.calls[-1][2]["ParentId"] == SERIES
    assert server.calls[-1][2]["SortBy"] == "DateCreated"


def test_upload_sends_base64_and_replaces_backdrops(serve: Any) -> None:
    tags = iter([{**FORD}, {**FORD, "BackdropImageTags": ["new"]}])
    jellyfin, server = serve(
        {
            f"GET /Items/{MOVIE}": lambda q: next(tags),
            f"DELETE /Items/{MOVIE}/Images/Backdrop": b"",
            f"POST /Items/{MOVIE}/Images/Backdrop": b"",
        }
    )
    assert jellyfin.upload(MOVIE, "art", b"\xff\xd8jpeg") == "new"
    deletes = [c for c in server.calls if c[0] == "DELETE"]
    assert len(deletes) == 2
    post = next(c for c in server.calls if c[0] == "POST")
    assert post[3] == base64.b64encode(b"\xff\xd8jpeg")


def test_labels_edit_tags_and_restore_refreshes(serve: Any) -> None:
    jellyfin, server = serve(
        {
            f"GET /Items/{MOVIE}": FORD,
            f"POST /Items/{MOVIE}": b"",
            f"DELETE /Items/{MOVIE}/Images/Primary": b"",
            f"POST /Items/{MOVIE}/Refresh": b"",
        }
    )
    jellyfin.remove_label({"ratingKey": MOVIE}, "posteryard-ignore")
    assert json.loads(server.calls[-1][3] or b"{}")["Tags"] == ["racing"]
    jellyfin.set_label(LIBRARY, "collection", MOVIE, "posteryard-collection")
    assert json.loads(server.calls[-1][3] or b"{}")["Tags"] == ["posteryard-collection"]
    jellyfin.restore({"ratingKey": MOVIE}, "thumb")
    assert server.calls[-1][1] == f"/Items/{MOVIE}/Refresh"
    jellyfin.lock({"ratingKey": MOVIE}, "poster")


def test_collection_calls(serve: Any) -> None:
    jellyfin, server = serve(
        {
            "POST /Collections": {"Id": SEASON},
            f"POST /Collections/{SEASON}/Items": b"",
            f"DELETE /Collections/{SEASON}/Items": b"",
            f"DELETE /Items/{SEASON}": b"",
            "/Items": {"Items": [FORD], "TotalRecordCount": 1},
        }
    )
    assert jellyfin.create_collection(LIBRARY, "show", "Netflix", [MOVIE, SERIES]) == SEASON
    assert server.calls[-1][2] == {"name": "Netflix", "ids": f"{MOVIE},{SERIES}"}
    jellyfin.add_to_collection(SEASON, [EPISODE])
    jellyfin.remove_from_collection(SEASON, EPISODE)
    jellyfin.delete_collection(SEASON)
    assert [i["ratingKey"] for i in jellyfin.collection_children(SEASON)] == [MOVIE]


def test_poster_bytes_and_selected(serve: Any) -> None:
    jellyfin, _ = serve({f"/Items/{MOVIE}/Images/Primary": b"\xff\xd8", f"/Items/{MOVIE}": FORD})
    assert jellyfin.poster_bytes({"ratingKey": MOVIE}) == b"\xff\xd8"
    assert jellyfin.selected(MOVIE, "poster") == "p1"
    assert jellyfin.selected(MOVIE, "art") == "b1"


def test_accessibility_from_jellyfin_streams(serve: Any) -> None:
    jellyfin, _ = serve({f"/Items/{MOVIE}": FORD})
    movie = jellyfin.item(MOVIE)
    assert movie is not None
    assert accessibility(movie["Media"], frozenset({Badge.SDH})) == [Badge.SDH]


def test_missing_numbers_are_left_out(serve: Any) -> None:
    unnumbered = {k: v for k, v in EPISODE_RAW.items() if k not in ("IndexNumber", "ParentIndexNumber")}
    jellyfin, _ = serve({f"/Items/{EPISODE}": unnumbered})
    episode = jellyfin.item(EPISODE)
    assert episode is not None and "index" not in episode and "parentIndex" not in episode


def test_changed_since_uses_the_save_date(serve: Any) -> None:
    jellyfin, server = serve({"/Items": {"Items": [FORD], "TotalRecordCount": 1}})
    assert [i["ratingKey"] for i in jellyfin.changed_since(LIBRARY, "movie", 1791065111)] == [MOVIE]
    assert [c[2].get("MinDateLastSaved") for c in server.calls if c[1] == "/Items"] == ["2026-10-03T22:05:11Z"]
