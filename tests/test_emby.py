import json
from typing import Any

import pytest

from posteryard import http
from posteryard.jellyfin import Emby
from posteryard.quality import AudioLevel, HdrLevel, VideoLevel, best
from tests.test_jellyfin import Server

BASE = "http://emby.example:8096"
USER = "f" * 32

UPGRADE = {
    "Id": "12",
    "Type": "Movie",
    "Name": "Upgrade",
    "Path": "/media/movies/Upgrade (2018)/Upgrade (2018).mkv",
    "DateCreated": "2026-10-06T00:25:29.0000000Z",
    "TagItems": [{"Name": "racing", "Id": 202}, {"Name": "Posteryard-Ignore", "Id": 203}],
    "ProviderIds": {"Tmdb": "500664", "Imdb": "tt6499752"},
    "MediaStreams": [
        {"Type": "Video", "Width": 3840, "Height": 2160, "VideoRange": "HDR 10", "ExtendedVideoType": "Hdr10",
         "ColorTransfer": "smpte2084", "DisplayTitle": "4K HDR 10 HEVC"},
        {"Type": "Audio", "Channels": 8, "Profile": "Dolby TrueHD + Dolby Atmos", "DisplayTitle": "TrueHD 7.1"},
    ],
}  # fmt: skip
SHOW = {"Id": "13", "Type": "Series", "Name": "The Office", "Path": "/media/tv/The Office (2005)",
        "ProviderIds": {"Tmdb": "2316", "IMDB": "tt0386676", "Tvdb": "73244"}}  # fmt: skip


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> tuple[Emby, Server]:
    server = Server(
        {
            "/Users": [{"Id": USER, "Policy": {"IsAdministrator": True}}],
            "/Library/VirtualFolders": [
                {"ItemId": "3", "Name": "Movies", "CollectionType": "movies", "Locations": ["/media/movies"]},
                {"ItemId": "8", "Name": "Shows", "CollectionType": "tvshows", "Locations": ["/media/tv"]},
            ],
            f"GET /Users/{USER}/Items/12": UPGRADE,
            f"GET /Users/{USER}/Items/13": SHOW,
            "POST /Items/12": b"",
            "/Items": {"Items": [], "TotalRecordCount": 0},
            "/System/Info/Public": {"Id": "36e979452bbf4fe2958b157ff8efccdd", "Version": "4.10.1.0"},
        }
    )
    monkeypatch.setattr(http, "request", server)
    return Emby(BASE, "example-key"), server


def test_items_use_numeric_ids_and_the_user_route(served: tuple[Emby, Server]) -> None:
    emby, fake = served
    movie = emby.item("12")
    assert movie is not None
    assert (movie["ratingKey"], movie["title"], movie["librarySectionTitle"]) == ("12", "Upgrade", "Movies")
    assert {label["tag"] for label in movie["Label"]} == {"racing", "Posteryard-Ignore"}
    assert ("GET", f"/Users/{USER}/Items/12") in [(c[0], c[1]) for c in fake.calls]
    quality = best(movie["Media"])
    assert (quality.video, quality.hdr, quality.audio) == (VideoLevel.UHD, HdrLevel.HDR10, AudioLevel.ATMOS)
    show = emby.item("13")
    assert show is not None and show["Guid"] == [
        {"id": "tmdb://2316"},
        {"id": "imdb://tt0386676"},
        {"id": "tvdb://73244"},
    ]
    assert emby.item("a" * 32) is None
    assert emby.item("../Users") is None
    with pytest.raises(ValueError, match="not a valid Emby item id"):
        emby.children("a" * 32)


def test_tags_are_saved_as_tag_items(served: tuple[Emby, Server]) -> None:
    emby, fake = served
    emby.remove_label({"ratingKey": "12"}, "posteryard-ignore")
    saved = json.loads(fake.calls[-1][3] or b"{}")
    assert saved["TagItems"] == [{"Name": "racing"}]
    assert "Tags" not in saved


def test_collections_belong_to_a_library(served: tuple[Emby, Server]) -> None:
    emby, fake = served
    assert list(emby.section_items("8", "collection", label="posteryard-collection")) == []
    query = fake.calls[-1][2]
    assert (query["ParentId"], query["IncludeItemTypes"], query["Tags"]) == ("8", "BoxSet", "posteryard-collection")


def test_dolby_vision_and_hdr10_plus(served: tuple[Emby, Server]) -> None:
    emby, fake = served
    for kind, level in (("DolbyVision", HdrLevel.DOLBY_VISION), ("Hdr10Plus", HdrLevel.HDR10_PLUS)):
        video: dict[str, Any] = {"Type": "Video", "Width": 3840, "Height": 2160, "ExtendedVideoType": kind}
        fake.routes[f"GET /Users/{USER}/Items/12"] = {**UPGRADE, "MediaStreams": [video]}
        movie = emby.item("12")
        assert movie is not None and best(movie["Media"]).hdr == level


def test_the_server_id_names_emby(served: tuple[Emby, Server]) -> None:
    emby, _ = served
    assert emby.server_id() == "emby:36e979452bbf4fe2958b157ff8efccdd"


def test_windows_library_paths_match(served: tuple[Emby, Server]) -> None:
    emby, fake = served
    fake.routes["/Library/VirtualFolders"] = [
        {"ItemId": "3", "Name": "Movies", "CollectionType": "movies", "Locations": ["D:\\Movies"]},
        {"ItemId": "4", "Name": "Films", "CollectionType": "movies", "Locations": ["D:\\Movies 4K\\"]},
    ]
    windows = {**UPGRADE, "Path": "D:\\Movies\\Upgrade (2018)\\Upgrade (2018).mkv"}
    fake.routes[f"GET /Users/{USER}/Items/12"] = windows
    movie = emby.item("12")
    assert movie is not None and movie["librarySectionTitle"] == "Movies"
    fake.routes[f"GET /Users/{USER}/Items/12"] = {**windows, "Path": "D:\\Movies 4K\\Upgrade.mkv"}
    movie = emby.item("12")
    assert movie is not None and movie["librarySectionTitle"] == "Films"
