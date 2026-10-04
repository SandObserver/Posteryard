import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from posteryard import overrides, pipeline
from posteryard.config import EpisodeMode
from posteryard.ocr import TextLine
from posteryard.quality import Badge, QualityMinimums
from posteryard.render.layers import APPLE_BLUE, APPLE_RED
from posteryard.render.lines import Badges, Caption, Label
from posteryard.tmdb import ImageRef, Images


def ref(path: str, language: str | None) -> ImageRef:
    return ImageRef(path, language, 2000, 3000, 5.0, 10)


class FakeTmdb:
    """A show with seasons 1 to 4. Seasons 1 and 3 have their own textless art."""

    def __init__(self, posters: list[ImageRef], seasons: int = 4) -> None:
        self.posters = posters
        self.seasons = seasons
        self.logos = [ref("/logo.png", "en")]

    def details(self, kind: str, tid: int) -> dict[str, Any]:
        return {"title": "Example Movie", "seasons": [{"season_number": n} for n in range(1, self.seasons + 1)]}

    def images(self, kind: str, tid: int) -> Images:
        backdrops = [
            ImageRef("/backdrop.jpg", None, 3840, 2160, 5, 10),
            ImageRef("/backdrop2.jpg", None, 3840, 2160, 4, 1),
        ]
        return Images(self.posters, backdrops, self.logos)

    def season_images(self, tid: int, season: int) -> Images:
        return Images([ref(f"/season{season}.jpg", None)] if season in (1, 3) else [], [], [])

    def all_titles(self, kind: str, tid: int) -> list[str]:
        return ["Example Movie", "Película de Ejemplo"]

    def watch_providers(self, kind: str, tid: int) -> dict[str, Any]:
        return {}

    def episode(self, tid: int, season: int, episode: int) -> dict[str, Any]:
        return {"still_path": f"/still-{season}-{episode}.jpg"}

    def find(self, source: str, external_id: str) -> dict[str, int]:
        self.finds = [*getattr(self, "finds", []), external_id]
        return {"movie": 77} if external_id == "tt0000077" else {}


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


class DatedPlex:
    def newest_added(self, section: str, kind: str, **filters: Any) -> int | None:
        if kind == "episode":
            return int(datetime(2026, 9, 29).timestamp())
        return int(datetime(2025, 1, 1).timestamp())


ITEM = {"ratingKey": "1", "title": "Example Movie", "Guid": [{"id": "tmdb://42"}], "Media": []}


def test_movie_uses_textless_art_that_shows_no_title() -> None:
    plans = pipeline.movie(context([ref("/english.jpg", None), ref("/textless.jpg", None)]), ITEM)
    assert [p.target for p in plans] == ["poster", "art"]
    assert plans[0].inputs["art"] == "/textless.jpg"
    assert plans[0].inputs["design"] == "tile"
    assert plans[0].inputs["label"] == Label("LEAVING IN 3 DAYS", APPLE_RED)
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
    assert plan.inputs["number"] == 7
    assert plan.inputs["service"] is None


def test_specials_keep_a_caption_and_no_number() -> None:
    ctx = context([ref("/textless.jpg", None)])
    title = ctx.title("tv", 42, "Example Show")
    plan = pipeline.season(ctx, title, {"ratingKey": "10", "index": 0})[0]
    assert plan.inputs["number"] is None
    assert plan.inputs["lines"] == [Caption("Specials")]


def test_a_show_added_long_ago_with_a_new_episode_says_so() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.server = DatedPlex()  # type: ignore[assignment]
    added = int(datetime(2025, 1, 1).timestamp())
    plan = pipeline.show(ctx, {**ITEM, "ratingKey": "5", "addedAt": added, "librarySectionID": 4})[0]
    assert plan.inputs["label"] == Label("NEW EPISODE", APPLE_BLUE)


def test_labels_can_be_turned_off_but_leaving_stays() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.labels = False
    plan = pipeline.movie(ctx, {**ITEM, "addedAt": int(datetime(2026, 9, 30).timestamp())})[0]
    assert plan.inputs["label"] == Label("LEAVING IN 3 DAYS", APPLE_RED)
    plan = pipeline.movie(ctx, {**ITEM, "ratingKey": "2", "addedAt": int(datetime(2026, 9, 30).timestamp())})[0]
    assert plan.inputs["label"] is None


def test_fingerprints_depend_on_the_design_version_not_the_package_version() -> None:
    plan = pipeline.Plan(
        "1", "poster", "Example", {"design": "tile", "art": "/a.jpg"}, lambda: Image.new("RGB", (1, 1))
    )
    assert plan.fingerprint == "88b5b08c3e5d3dcb12fb17c06a9f7857"


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


class FakePlex:
    """A show (1) with seasons 1 and 2 (11, 12), each with two episodes."""

    name = "Plex"
    url = "http://plex.example:32400"

    def __init__(self) -> None:
        show = {"ratingKey": "1", "type": "show", "title": "Example Show", "Guid": [{"id": "tmdb://42"}]}
        self.items: dict[str, dict[str, Any]] = {"1": show}
        self.kids: dict[str, list[dict[str, Any]]] = {"1": []}
        for season in (1, 2):
            key = str(10 + season)
            self.items[key] = {"ratingKey": key, "type": "season", "index": season, "parentRatingKey": "1"}
            self.kids["1"].append(self.items[key])
            self.kids[key] = [
                {"ratingKey": f"{key}{n}", "type": "episode", "index": n, "parentIndex": season,
                 "grandparentRatingKey": "1", "title": f"Episode {n}"}
                for n in (1, 2)
            ]  # fmt: skip

    def item(self, key: str) -> dict[str, Any] | None:
        return self.items.get(key)

    def children(self, key: str) -> list[dict[str, Any]]:
        return self.kids.get(key, [])


def test_preview_of_a_show_covers_seasons_and_episodes() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.server = FakePlex()  # type: ignore[assignment]
    plans = pipeline.plan_preview(ctx, "1", episodes=1)
    assert [(p.rating_key, p.target) for p in plans] == [
        ("1", "poster"), ("1", "art"), ("11", "poster"), ("111", "thumb"), ("12", "poster"), ("121", "thumb"),
    ]  # fmt: skip
    still = plans[3]
    assert still.inputs == {"design": "episode", "still": "/still-1-1.jpg", "mode": "plain"}
    assert still.draw().size == (1920, 1080)
    assert [p.rating_key for p in pipeline.plan_preview(ctx, "12", episodes=None)] == ["12", "121", "122"]


def test_episode_modes() -> None:
    ctx = context([ref("/textless.jpg", None)])
    title = ctx.title("tv", 42, "Example Show")
    item = {"ratingKey": "111", "index": 1, "parentIndex": 1, "title": "Pilot"}
    ctx.episodes = EpisodeMode.TITLED
    titled = pipeline.episode(ctx, title, item)[0]
    assert titled.inputs["title"] == "Pilot"
    assert titled.draw().size == (1920, 1080)
    ctx.episodes = EpisodeMode.OFF
    assert pipeline.episode(ctx, title, item) == []


def test_a_title_without_a_logo_is_set_in_text() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.tmdb.logos = []  # type: ignore[attr-defined]
    plan = pipeline.movie(ctx, ITEM)[0]
    assert plan.inputs["logo"] is None
    assert plan.inputs["text_logo"] == "Example Movie"
    assert plan.draw().size == (1000, 1500)


def test_accessibility_badges_get_their_own_line() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.accessibility = frozenset({Badge.SDH, Badge.AD})
    media = [
        {"Part": [{"Stream": [{"streamType": 3, "hearingImpaired": True}, {"streamType": 2, "title": "English AD"}]}]}
    ]
    plan = pipeline.movie(ctx, {**ITEM, "ratingKey": "2", "Media": media})[0]
    assert plan.inputs["lines"] == [Badges((Badge.SDH, Badge.AD))]


def test_preview_of_a_missing_item_fails() -> None:
    ctx = context([ref("/textless.jpg", None)])
    with pytest.raises(pipeline.NotFoundError, match="configured"):
        pipeline.plan_preview(ctx, "1")
    ctx.server = FakePlex()  # type: ignore[assignment]
    with pytest.raises(pipeline.NotFoundError, match="no item"):
        pipeline.plan_preview(ctx, "99")


def test_tmdb_preview_without_plex() -> None:
    ctx = context([ref("/textless.jpg", None)])
    assert [p.target for p in pipeline.plan_tmdb(ctx, "movie", 42)] == ["poster", "art"]
    plans = pipeline.plan_tmdb(ctx, "tv", 42, [1, 3])
    assert [p.inputs.get("number") for p in plans] == [None, None, 1, 3]


def test_a_title_without_a_tmdb_id_is_not_found() -> None:
    with pytest.raises(pipeline.NotFoundError, match="no TMDB id"):
        pipeline.movie(context([ref("/textless.jpg", None)]), {**ITEM, "Guid": []})


def test_an_imdb_id_is_looked_up_once_and_remembered() -> None:
    ctx = context([ref("/textless.jpg", None)])
    item = {**ITEM, "Guid": [{"id": "imdb://tt0000077"}]}
    assert pipeline.movie(ctx, item)[0].inputs["art"] == "/textless.jpg"
    assert ctx.titles[("movie", 77)]
    ctx.lookups.clear()
    pipeline.movie(ctx, item)
    assert ctx.tmdb.finds == ["tt0000077"]  # type: ignore[attr-defined]
    with pytest.raises(pipeline.NotFoundError, match="no TMDB id"):
        pipeline.movie(ctx, {**ITEM, "Guid": [{"id": "imdb://tt0000078"}]})


class CollectionPlex:
    def collection_children(self, key: str) -> list[dict[str, Any]]:
        return (
            [
                {"ratingKey": "1", "type": "show", "title": "Old Show", "addedAt": 100, "Guid": [{"id": "tmdb://41"}]},
                {"ratingKey": "2", "type": "show", "title": "New Show", "addedAt": 200, "Guid": [{"id": "tmdb://42"}]},
                {"ratingKey": "3", "type": "show", "title": "No Id", "addedAt": 300},
            ]
            if key != "9"
            else []
        )


def test_collections_take_art_from_their_newest_member() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.server = CollectionPlex()  # type: ignore[assignment]
    channel = pipeline.plan_item(ctx, {"ratingKey": "7", "type": "collection", "title": "Netflix"})[0]
    assert channel.inputs["design"] == "channel"
    assert channel.inputs["service"] == "netflix"
    assert channel.inputs["featured"] == "New Show"
    assert channel.draw().size == (1000, 1500)
    plain = pipeline.plan_item(ctx, {"ratingKey": "8", "type": "collection", "title": "Star Wars"})[0]
    assert plain.inputs == {"design": "collection", "art": "/textless.jpg", "title": "Star Wars"}
    assert plain.draw().size == (1000, 1500)
    assert pipeline.plan_item(ctx, {"ratingKey": "9", "type": "collection", "title": "Empty"}) == []
