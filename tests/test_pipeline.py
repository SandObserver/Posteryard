import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from posteryard import overrides, pipeline
from posteryard.ocr import TextLine
from posteryard.quality import QualityMinimums
from posteryard.tmdb import ImageRef, Images


def ref(path: str, language: str | None) -> ImageRef:
    return ImageRef(path, language, 2000, 3000, 5.0, 10)


class FakeTmdb:
    """A show with seasons 1 to 4. Seasons 1 and 3 have their own textless art."""

    def __init__(self, posters: list[ImageRef], seasons: int = 4) -> None:
        self.posters = posters
        self.seasons = seasons

    def details(self, kind: str, tid: int) -> dict[str, Any]:
        return {"title": "Example Movie", "seasons": [{"season_number": n} for n in range(1, self.seasons + 1)]}

    def images(self, kind: str, tid: int) -> Images:
        backdrops = [
            ImageRef("/backdrop.jpg", None, 3840, 2160, 5, 10),
            ImageRef("/backdrop2.jpg", None, 3840, 2160, 4, 1),
        ]
        return Images(self.posters, backdrops, [ref("/logo.png", "en")])

    def season_images(self, tid: int, season: int) -> Images:
        return Images([ref(f"/season{season}.jpg", None)] if season in (1, 3) else [], [], [])

    def english_titles(self, kind: str, tid: int) -> list[str]:
        return ["Example Movie"]

    def all_titles(self, kind: str, tid: int) -> list[str]:
        return ["Example Movie", "Película de Ejemplo"]

    def watch_providers(self, kind: str, tid: int) -> dict[str, Any]:
        return {}

    def episode(self, tid: int, season: int, episode: int) -> dict[str, Any]:
        return {"still_path": f"/still-{season}-{episode}.jpg"}


TEXT = {"/foreign.jpg": "PELICULA DE EJEMPLO", "/english.jpg": "EXAMPLE MOVIE"}


def fetch(path: str) -> Image.Image:
    if path.endswith(".png"):
        logo = Image.new("RGBA", (600, 120), (0, 0, 0, 0))
        ImageDraw.Draw(logo).rectangle((0, 0, 599, 119), fill=(255, 255, 255, 255))
        return logo
    noise = np.random.default_rng(int(hashlib.sha256(path.encode()).hexdigest()[:8], 16)).integers(0, 256, (8, 9))
    image = Image.fromarray(noise.astype(np.uint8)).convert("RGB").resize((200, 300), Image.Resampling.NEAREST)
    image.info["path"] = path
    return image


def read(image: Image.Image) -> list[TextLine]:
    text = TEXT.get(str(image.info.get("path")), "")
    return [TextLine(text, 0.99, 0.06, 0.6)] if text else []


def context(posters: list[ImageRef], seasons: int = 4) -> pipeline.Context:
    return pipeline.Context(
        tmdb=FakeTmdb(posters, seasons),  # type: ignore[arg-type]
        minimums=QualityMinimums(),
        regions=("CA",),
        action_days={"1": date(2026, 10, 4)},
        today=date(2026, 10, 1),
        read=read,
        fetch=fetch,
    )


ITEM = {"ratingKey": "1", "title": "Example Movie", "Guid": [{"id": "tmdb://42"}], "Media": []}


def test_movie_uses_textless_art_that_shows_no_title() -> None:
    plans = pipeline.movie(context([ref("/english.jpg", None), ref("/textless.jpg", None)]), ITEM)
    assert [p.target for p in plans] == ["poster", "art"]
    assert plans[0].inputs["art"] == "/textless.jpg"
    assert plans[0].inputs["design"] == "tile"
    assert plans[0].inputs["leaving"] == "LEAVING IN 3 DAYS"
    assert plans[0].draw().size == (1000, 1500)


def test_seasons_get_their_own_art_then_unused_series_art_then_the_show_art() -> None:
    ctx = context([ref("/textless.jpg", None)])
    title = ctx.title("tv", 42, "Example Show")
    art = {
        n: pipeline.season(ctx, title, {"ratingKey": str(10 + n), "index": n, "parentRatingKey": "1"})[0].inputs["art"]
        for n in (1, 2, 3, 4)
    }
    assert art[1] == "/season1.jpg"
    assert art[3] == "/season3.jpg"
    assert art[2] == "/backdrop.jpg"
    assert art[4] == "/backdrop2.jpg"
    assert len(set(art.values())) == 4


def test_the_same_picture_under_another_name_is_not_reused() -> None:
    ctx = context([ref("/textless.jpg", None)])
    real = ctx.fetch
    ctx.fetch = lambda path: real("/season1.jpg" if path == "/backdrop.jpg" else path)
    title = ctx.title("tv", 42, "Example Show")
    art = pipeline.season(ctx, title, {"ratingKey": "12", "index": 2})[0].inputs["art"]
    assert art == "/backdrop2.jpg"


def test_a_season_falls_back_to_the_show_art_when_nothing_is_left() -> None:
    ctx = context([ref("/textless.jpg", None)], seasons=7)
    title = ctx.title("tv", 42, "Example Show")
    plan = pipeline.season(ctx, title, {"ratingKey": "17", "index": 7})[0]
    assert plan.inputs["art"] == "/textless.jpg"
    assert plan.inputs["label"] == "Season 7"


def test_fingerprints_depend_on_the_design_version_not_the_package_version() -> None:
    plan = pipeline.Plan(
        "1", "poster", "Example", {"design": "tile", "art": "/a.jpg"}, lambda: Image.new("RGB", (1, 1))
    )
    assert plan.fingerprint == "d4fa7f2c453257164d8f6fec8f560e35"


def test_custom_art_replaces_the_chosen_art(tmp_path: Path) -> None:
    custom = tmp_path / "1-abc.jpg"
    Image.new("RGB", (800, 1200), (200, 30, 30)).save(custom)
    ctx = context([ref("/textless.jpg", None)])
    ctx.overrides = lambda key: overrides.Override(custom=str(custom), source="command") if key == "1" else None
    plan = pipeline.movie(ctx, ITEM)[0]
    assert plan.inputs["art"] == f"file:{custom}"
    assert plan.inputs["override"] == "1-abc.jpg"
    assert plan.draw().size == (1000, 1500)


def test_next_art_skips_the_current_picture() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({"/textless.jpg"}))
    plan = pipeline.movie(ctx, ITEM)[0]
    assert plan.inputs["art"] == "/backdrop.jpg"
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({"/textless.jpg", "/backdrop.jpg"}))
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop2.jpg"


def test_without_overrides_fingerprints_stay_the_same() -> None:
    plan = pipeline.movie(context([ref("/textless.jpg", None)]), ITEM)[0]
    assert "override" not in plan.inputs
