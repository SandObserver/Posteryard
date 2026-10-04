import io
from typing import Any

import pytest
from PIL import Image

from posteryard import http
from posteryard.fanart import Fanart, is_fanart

MOVIE = {
    "movieposter": [
        {"url": "http://assets.fanart.tv/fanart/movies/1/movieposter/titled.jpg", "lang": "en", "likes": "9"},
        {"url": "http://assets.fanart.tv/fanart/movies/1/movieposter/clean.jpg", "lang": "00", "likes": "3"},
        {"url": "https://elsewhere.example/poster.jpg", "lang": "00", "likes": "99"},
    ],
    "hdmovielogo": [
        {"url": "https://assets.fanart.tv/fanart/movies/1/hdmovielogo/logo.png", "lang": "en", "likes": "1"}
    ],
    "moviebackground": [{"url": "https://assets.fanart.tv/fanart/movies/1/moviebackground/b.jpg", "lang": ""}],
}
SHOW = {
    "tvposter": [],
    "seasonposter": [
        {
            "url": "https://assets.fanart.tv/fanart/tv/5/seasonposter/s2-low.jpg",
            "lang": "00",
            "season": "2",
            "likes": "1",
        },
        {
            "url": "https://assets.fanart.tv/fanart/tv/5/seasonposter/s2-top.jpg",
            "lang": "00",
            "season": "2",
            "likes": "4",
        },
        {"url": "https://assets.fanart.tv/fanart/tv/5/seasonposter/all.jpg", "lang": "00", "season": "all"},
    ],
}


def answer(routes: dict[str, Any], seen: list[tuple[str, dict[str, str]]]) -> Any:
    def get_json(url: str, headers: dict[str, str] | None = None) -> Any:
        seen.append((url, headers or {}))
        path = url.removeprefix("https://webservice.fanart.tv/v3")
        if path not in routes:
            raise http.HttpError(404, url)
        return routes[path]

    return get_json


def test_movie_images_are_textless_marked_and_limited_to_fanart_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(http, "get_json", answer({"/movies/603": MOVIE}, seen))
    found = Fanart("example-key").images("movie", 603, None)
    assert [(r.path, r.language) for r in found.images.posters] == [
        ("https://assets.fanart.tv/fanart/movies/1/movieposter/titled.jpg", "en"),
        ("https://assets.fanart.tv/fanart/movies/1/movieposter/clean.jpg", None),
    ]
    assert [r.path for r in found.images.textless_posters()] == [
        "https://assets.fanart.tv/fanart/movies/1/movieposter/clean.jpg"
    ]
    assert found.images.logos_in(["en"])[0].width == 800
    assert found.images.textless_backdrops()[0].width == 1920
    assert seen[0][1] == {"api-key": "example-key"}
    assert "example-key" not in seen[0][0]


def test_shows_need_a_tvdb_id_and_season_posters_are_grouped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(http, "get_json", answer({"/tv/5": SHOW}, seen))
    fanart = Fanart("example-key")
    assert fanart.images("tv", 1, None).seasons == {}
    assert seen == []
    seasons = fanart.images("tv", 1, 5).seasons
    assert list(seasons) == [2]
    assert [r.path.rsplit("/", 1)[1] for r in seasons[2]] == ["s2-top.jpg", "s2-low.jpg"]
    assert fanart.images("tv", 1, 6).images.posters == []


def test_only_fanart_assets_are_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (100, 150)).save(buffer, "JPEG")
    monkeypatch.setattr(http, "request", lambda method, url, **kwargs: buffer.getvalue())
    assert Fanart.image("https://assets.fanart.tv/fanart/movies/1/movieposter/a.jpg").size == (100, 150)
    assert is_fanart("http://assets.fanart.tv/x.jpg")
    with pytest.raises(ValueError, match=r"not a fanart\.tv image"):
        Fanart.image("https://elsewhere.example/a.jpg")
