from datetime import date
from typing import Any

from PIL import Image, ImageDraw

from posteryard import pipeline
from posteryard.ocr import TextLine
from posteryard.quality import QualityMinimums
from posteryard.tmdb import ImageRef, Images


def ref(path: str, language: str | None) -> ImageRef:
    return ImageRef(path, language, 2000, 3000, 5.0, 10)


class FakeTmdb:
    def __init__(self, posters: list[ImageRef]) -> None:
        self.posters = posters

    def details(self, kind: str, tid: int) -> dict[str, Any]:
        return {"title": "Example Movie"}

    def images(self, kind: str, tid: int) -> Images:
        return Images(self.posters, [ImageRef("/backdrop.jpg", None, 3840, 2160, 5, 10)], [ref("/logo.png", "en")])

    def english_titles(self, kind: str, tid: int) -> list[str]:
        return ["Example Movie"]

    def all_titles(self, kind: str, tid: int) -> list[str]:
        return ["Example Movie", "Película de Ejemplo"]

    def watch_providers(self, kind: str, tid: int) -> dict[str, Any]:
        return {}


TEXT = {"/foreign.jpg": "PELICULA DE EJEMPLO", "/english.jpg": "EXAMPLE MOVIE", "/textless.jpg": ""}


def fetch(path: str) -> Image.Image:
    if path.endswith(".png"):
        logo = Image.new("RGBA", (600, 120), (0, 0, 0, 0))
        ImageDraw.Draw(logo).rectangle((0, 0, 599, 119), fill=(255, 255, 255, 255))
        return logo
    image = Image.new("RGB", (2000, 3000), (30, 40, 50))
    image.info["path"] = path
    return image


def read(image: Image.Image) -> list[TextLine]:
    text = TEXT.get(str(image.info.get("path")), "")
    return [TextLine(text, 0.99, 0.06, 0.6)] if text else []


def context(posters: list[ImageRef]) -> pipeline.Context:
    return pipeline.Context(
        tmdb=FakeTmdb(posters),  # type: ignore[arg-type]
        minimums=QualityMinimums(),
        regions=("CA",),
        action_days={"1": date(2026, 10, 4)},
        today=date(2026, 10, 1),
        read=read,
        fetch=fetch,
    )


ITEM = {"ratingKey": "1", "title": "Example Movie", "Guid": [{"id": "tmdb://42"}], "Media": []}


def test_movie_uses_the_english_poster_that_shows_its_title() -> None:
    outputs = pipeline.movie(context([ref("/foreign.jpg", "en"), ref("/english.jpg", "en")]), ITEM)
    assert [o.target for o in outputs] == ["poster", "art"]
    assert outputs[0].notes == ["studio poster /english.jpg"]


def test_movie_falls_back_to_textless_art_and_logo() -> None:
    outputs = pipeline.movie(context([ref("/foreign.jpg", "en"), ref("/textless.jpg", None)]), ITEM)
    assert outputs[0].notes[0] == "no English poster with a readable title"
    assert "fallback design, art /textless.jpg" in outputs[0].notes


def test_leaving_label_comes_from_maintainerr() -> None:
    assert context([]).leaving("1") == "LEAVING IN 3 DAYS"
    assert context([]).leaving("2") is None
