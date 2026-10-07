import threading
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from posteryard import apple, overrides, pipeline
from posteryard import http as posteryard_http
from posteryard.artwork import MemoryChoices
from posteryard.automarks import AutoMarks
from posteryard.config import EpisodeMode
from posteryard.fanart import Fanart, FanartImages
from posteryard.quality import Badge
from posteryard.render import designs
from posteryard.render.layers import APPLE_BLUE, APPLE_RED
from posteryard.render.lines import Badges, Caption, Label
from posteryard.server import Item
from posteryard.services import Offer
from posteryard.tmdb import Images, Kind, RegionOffers, Tmdb
from tests.fakes import FakeServer, context, fetch, ref, tmdb_of


class DatedPlex(FakeServer):
    def newest_added(self, section_key: str, kind: str, **filters: Any) -> int | None:
        if kind == "episode":
            return int(datetime(2026, 9, 29).timestamp())
        return int(datetime(2025, 1, 1).timestamp())


ITEM: Item = {"ratingKey": "1", "title": "Example Movie", "Guid": [{"id": "tmdb://42"}], "Media": []}


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
    ctx.server = DatedPlex()
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
    assert plan.fingerprint == "7c4ab6a238386cf3ba84f81e6d778b03"


def test_custom_art_replaces_the_chosen_art(tmp_path: Path) -> None:
    custom = tmp_path / "1-abc.jpg"
    Image.new("RGB", (800, 1200), (200, 30, 30)).save(custom)
    ctx = context([ref("/textless.jpg", None)])
    ctx.overrides = lambda key: overrides.Override(custom=str(custom), source="command") if key == "1" else None
    plan = pipeline.movie(ctx, ITEM)[0]
    assert plan.inputs["art"] == f"file:{custom}"
    assert plan.inputs["override"] == "1-abc.jpg"
    assert plan.draw().size == (1000, 1500)


def test_custom_art_works_when_no_source_has_clean_art(tmp_path: Path) -> None:
    custom = tmp_path / "1-abc.jpg"
    Image.new("RGB", (800, 1200), (200, 30, 30)).save(custom)
    ctx = context([])
    tmdb_of(ctx).backdrops = []
    with pytest.raises(pipeline.NotFoundError):
        pipeline.movie(ctx, ITEM)
    ctx.overrides = lambda key: overrides.Override(custom=str(custom), source="command")
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == f"file:{custom}"


def test_next_art_skips_the_current_picture() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({"/textless.jpg"}))
    plan = pipeline.movie(ctx, ITEM)[0]
    assert plan.inputs["art"] == "/backdrop.jpg"
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({"/textless.jpg", "/backdrop.jpg"}))
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop2.jpg"


def test_next_art_steps_past_skipped_art_the_server_deleted() -> None:
    ctx = context([ref("/textless.jpg", None)])
    real = ctx.fetch

    def gone(path: str) -> Image.Image:
        if path == "/deleted.jpg":
            raise posteryard_http.HttpError(404, "https://image.tmdb.org/t/p/original/deleted.jpg")
        return real(path)

    ctx.fetch = gone
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({"/textless.jpg", "/deleted.jpg"}))
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop.jpg"


def test_an_outage_while_comparing_art_still_fails_the_item() -> None:
    ctx = context([ref("/textless.jpg", None)])

    def down(path: str) -> Image.Image:
        raise posteryard_http.HttpError(503, "https://image.tmdb.org/t/p/original/x.jpg")

    ctx.fetch = down
    with pytest.raises(posteryard_http.HttpError):
        ctx.same_picture("/a.jpg", "/b.jpg")


def test_without_overrides_fingerprints_stay_the_same() -> None:
    plan = pipeline.movie(context([ref("/textless.jpg", None)]), ITEM)[0]
    assert "override" not in plan.inputs


class FakePlex(FakeServer):
    """A show (1) with seasons 1 and 2 (11, 12), each with two episodes."""

    def __init__(self) -> None:
        show: Item = {"ratingKey": "1", "type": "show", "title": "Example Show", "Guid": [{"id": "tmdb://42"}]}
        self.items: dict[str, Item] = {"1": show}
        self.kids: dict[str, list[Item]] = {"1": []}
        for season in (1, 2):
            key = str(10 + season)
            self.items[key] = {"ratingKey": key, "type": "season", "index": season, "parentRatingKey": "1"}
            self.kids["1"].append(self.items[key])
            self.kids[key] = [
                {"ratingKey": f"{key}{n}", "type": "episode", "index": n, "parentIndex": season,
                 "grandparentRatingKey": "1", "title": f"Episode {n}"}
                for n in (1, 2)
            ]  # fmt: skip

    def item(self, rating_key: str) -> Item | None:
        return self.items.get(rating_key)

    def children(self, rating_key: str) -> list[Item]:
        return self.kids.get(rating_key, [])


def test_preview_of_a_show_covers_seasons_and_episodes() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.server = FakePlex()
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
    item: Item = {"ratingKey": "111", "index": 1, "parentIndex": 1, "title": "Pilot"}
    ctx.episodes = EpisodeMode.TITLED
    titled = pipeline.episode(ctx, title, item)[0]
    assert titled.inputs["title"] == "Pilot"
    assert titled.draw().size == (1920, 1080)
    ctx.episodes = EpisodeMode.OFF
    assert pipeline.episode(ctx, title, item) == []


def test_a_title_without_a_logo_is_set_in_text() -> None:
    ctx = context([ref("/textless.jpg", None)])
    tmdb_of(ctx).logos = []
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
    ctx.server = FakePlex()
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
    item: Item = {**ITEM, "Guid": [{"id": "imdb://tt0000077"}]}
    assert pipeline.movie(ctx, item)[0].inputs["art"] == "/textless.jpg"
    assert ctx.titles[("movie", 77)]
    ctx.lookups.clear()
    pipeline.movie(ctx, item)
    assert tmdb_of(ctx).finds == ["tt0000077"]
    with pytest.raises(pipeline.NotFoundError, match="no TMDB id"):
        pipeline.movie(ctx, {**ITEM, "Guid": [{"id": "imdb://tt0000078"}]})


def test_forget_drops_downloaded_images_and_expired_lookups() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.fetch = lru_cache(maxsize=pipeline.FETCH_CACHE)(fetch)
    pipeline.movie(ctx, ITEM)
    old = time.monotonic() - pipeline.TITLE_CACHE_SECONDS
    ctx.lookups[("old",)] = (old, None)
    fresh = set(ctx.lookups) - {("old",)}
    ctx.forget()
    assert ctx.fetch.cache_info().currsize == 0
    assert set(ctx.lookups) == fresh
    assert ("movie", 42) in ctx.titles


class CollectionPlex(FakeServer):
    def collection_children(self, rating_key: str) -> list[Item]:
        return (
            [
                {"ratingKey": "1", "type": "show", "title": "Old Show", "addedAt": 100, "Guid": [{"id": "tmdb://41"}]},
                {"ratingKey": "2", "type": "show", "title": "New Show", "addedAt": 200, "Guid": [{"id": "tmdb://42"}]},
                {"ratingKey": "3", "type": "show", "title": "No Id", "addedAt": 300},
            ]
            if rating_key != "9"
            else []
        )


def test_collections_take_art_from_their_newest_member() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.server = CollectionPlex()
    channel = pipeline.plan_item(ctx, {"ratingKey": "7", "type": "collection", "title": "Netflix"})[0]
    assert channel.inputs["design"] == "channel"
    assert channel.inputs["service"] == "netflix"
    assert channel.inputs["featured"] == "New Show"
    assert channel.draw().size == (1000, 1500)
    plain = pipeline.plan_item(ctx, {"ratingKey": "8", "type": "collection", "title": "Star Wars"})[0]
    assert plain.inputs == {"design": "category", "art": "/textless.jpg", "title": "Star Wars", "palette": "sports"}
    assert plain.draw().size == (1000, 1500)
    assert pipeline.plan_item(ctx, {"ratingKey": "9", "type": "collection", "title": "Empty"}) == []


class FakeFanart(Fanart):
    def __init__(self) -> None:
        super().__init__("example")
        self.calls = 0

    def images(self, kind: Kind, tmdb_id: int, tvdb_id: int | None) -> FanartImages:
        self.calls += 1
        poster = ref("https://assets.fanart.tv/fanart/movies/42/movieposter/clean.jpg", None)
        logo = ref("https://assets.fanart.tv/fanart/movies/42/hdmovielogo/logo.png", "en")
        return FanartImages(Images([poster], [], [logo]))


def test_fanart_is_used_only_when_tmdb_has_nothing_usable() -> None:
    ctx = context([ref("/english.jpg", "en")])
    tmdb = tmdb_of(ctx)
    tmdb.backdrops, tmdb.logos = [], []
    with pytest.raises(pipeline.NotFoundError, match="TMDB has no textless art"):
        pipeline.movie(ctx, ITEM)
    ctx.fanart = FakeFanart()
    poster = pipeline.movie(ctx, ITEM)[0]
    assert poster.inputs["art"] == "https://assets.fanart.tv/fanart/movies/42/movieposter/clean.jpg"
    assert poster.inputs["logo"] == "https://assets.fanart.tv/fanart/movies/42/hdmovielogo/logo.png"
    with_tmdb_art = context([ref("/textless.jpg", None)])
    fanart = FakeFanart()
    with_tmdb_art.fanart = fanart
    assert pipeline.movie(with_tmdb_art, ITEM)[0].inputs["art"] == "/textless.jpg"
    assert fanart.calls == 0


class BrokenFanart(Fanart):
    def images(self, kind: Kind, tmdb_id: int, tvdb_id: int | None) -> FanartImages:
        raise posteryard_http.RequestError("ConnectionError", "https://webservice.fanart.tv/v3/movies/42")


def test_a_fanart_outage_fails_the_item_instead_of_changing_its_poster() -> None:
    ctx = context([ref("/english.jpg", "en")])
    tmdb = tmdb_of(ctx)
    tmdb.backdrops, tmdb.logos = [], []
    ctx.fanart = BrokenFanart("example")
    with pytest.raises(posteryard_http.RequestError):
        pipeline.movie(ctx, ITEM)
    assert not any(key[0] == "fanart" for key in ctx.lookups)


def test_lookups_are_bounded_and_details_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "LOOKUP_SIZE", 3)
    ctx = context([ref("/textless.jpg", None)])
    for n in "01234":
        ctx.remember(("n", n), str)
    assert [key[1] for key in ctx.lookups] == ["2", "3", "4"]
    monkeypatch.setattr(ctx.tmdb, "details", lambda kind, tid: {"name": "A", "images": {"posters": []}})
    assert ctx.details("tv", 7) == {"name": "A"}


class ImdbCollection(FakeServer):
    def collection_children(self, rating_key: str) -> list[Item]:
        return [{"ratingKey": "1", "type": "movie", "title": "Old", "addedAt": 1, "Guid": [{"id": "imdb://tt0000077"}]}]


def test_collections_use_imdb_ids_too() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.server = ImdbCollection()
    plans = pipeline.collection(ctx, {"ratingKey": "9", "type": "collection", "title": "Favourites"})
    assert plans[0].inputs["art"] == "/textless.jpg"


APPLE_URL = "https://is1-ssl.mzstatic.com/image/thumb/Features/v4/ab/cd/art.jpg/1680x3636nr.jpg"


def test_a_textless_tmdb_poster_comes_before_apple_art(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple, "find", lambda kind, details, region: APPLE_URL)
    ctx = context([ref("/textless.jpg", None)])
    ctx.apple_region = "CA"
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/textless.jpg"


class RejectingMarks(AutoMarks):
    def get(self, offer: Offer, tmdb: Tmdb | None = None) -> str | None:
        return None


def test_a_left_out_mark_is_not_replaced_by_the_next_service(tmp_path: Path) -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.marks = RejectingMarks(tmp_path, MemoryChoices())
    providers: dict[str, RegionOffers] = {"CA": {"flatrate": [
        {"provider_id": 510, "provider_name": "Discovery+", "logo_path": "/d.jpg", "display_priority": 1},
        {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/n.jpg", "display_priority": 2},
    ]}}  # fmt: skip
    assert ctx.service(providers) is None


def test_apple_art_comes_before_backdrops_and_art_next_steps_past_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def find(kind: str, details: Any, region: str) -> str:
        calls.append(region)
        return APPLE_URL

    monkeypatch.setattr(apple, "find", find)
    ctx = context([ref("/english.jpg", "en")])
    ctx.apple_region = "CA"
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == APPLE_URL
    pipeline.movie(ctx, ITEM)
    assert calls == ["CA"]
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({APPLE_URL}))
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop.jpg"


def test_apple_art_is_looked_up_again_after_a_month(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(apple, "find", lambda kind, details, region: calls.append(region))
    ctx = context([ref("/english.jpg", "en")])
    ctx.apple_region = "CA"
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop.jpg"
    ctx.today += timedelta(days=pipeline.APPLE_ART_DAYS)
    pipeline.movie(ctx, ITEM)
    assert len(calls) == 2


def test_an_apple_outage_keeps_the_last_apple_art_or_uses_other_art(monkeypatch: pytest.MonkeyPatch) -> None:
    found: list[str | None] = [APPLE_URL]

    def find(kind: str, details: Any, region: str) -> str | None:
        if not found:
            raise posteryard_http.HttpError(429, "https://www.wikidata.org/wiki/Special:EntityData/Q1.json")
        return found.pop()

    monkeypatch.setattr(apple, "find", find)
    ctx = context([ref("/english.jpg", "en")])
    ctx.apple_region = "CA"
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == APPLE_URL
    ctx.today += timedelta(days=pipeline.APPLE_ART_DAYS)
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == APPLE_URL
    other: Item = {**ITEM, "Guid": [{"id": "tmdb://43"}]}
    assert pipeline.movie(ctx, other)[0].inputs["art"] == "/backdrop.jpg"


def test_apple_art_that_cannot_be_loaded_falls_back_to_other_art(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple, "find", lambda kind, details, region: APPLE_URL)
    ctx = context([ref("/english.jpg", "en")])
    ctx.apple_region = "CA"

    def fetch_or_fail(path: str) -> Image.Image:
        if path == APPLE_URL:
            raise posteryard_http.HttpError(503, path)
        return fetch(path)

    ctx.fetch = fetch_or_fail
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop.jpg"


def test_light_art_takes_a_dark_logo_and_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context([ref("/textless.jpg", None)])
    tmdb_of(ctx).logos = [ref("/logo.png", "en"), ref("/dark.png", "en")]
    ctx.fetch = lambda path: (
        Image.new("RGBA", (600, 120), (20, 20, 20, 255))
        if path == "/dark.png"
        else Image.new("RGBA", (600, 120), (255, 255, 255, 255))
        if path.endswith(".png")
        else Image.new("RGB", (200, 300), "white")
    )
    poster = pipeline.movie(ctx, ITEM)[0]
    assert poster.inputs["logo"] == "/logo.png"
    assert poster.inputs["logo_ink"] == "dark"
    assert poster.inputs["ink"] == "dark"
    assert "fade" not in poster.inputs


def test_a_coloured_logo_on_light_art_uses_the_dark_logo(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context([ref("/textless.jpg", None)])
    tmdb_of(ctx).logos = [ref("/logo.png", "en"), ref("/dark.png", "en")]

    def fetch(path: str) -> Image.Image:
        if path == "/dark.png":
            return Image.new("RGBA", (600, 120), (20, 20, 20, 255))
        if path.endswith(".png"):
            coloured = Image.new("RGBA", (600, 120), (255, 255, 255, 255))
            coloured.paste((240, 40, 40, 255), (0, 0, 300, 120))
            return coloured
        return Image.new("RGB", (200, 300), "white")

    ctx.fetch = fetch
    poster = pipeline.movie(ctx, ITEM)[0]
    assert poster.inputs["logo"] == "/dark.png"
    assert "logo_ink" not in poster.inputs


class DownFanart(Fanart):
    def images(self, kind: Kind, tmdb_id: int, tvdb_id: int | None) -> FanartImages:
        raise posteryard_http.HttpError(503, "https://webservice.fanart.tv/v3/movies/42")


def test_custom_art_needs_no_art_source(tmp_path: Path) -> None:
    custom = tmp_path / "1-abc.jpg"
    Image.new("RGB", (800, 1200), (200, 30, 30)).save(custom)
    ctx = context([])
    tmdb_of(ctx).backdrops = []
    ctx.fanart = DownFanart("example")
    ctx.overrides = lambda key: overrides.Override(custom=str(custom), source="command")
    plans = pipeline.movie(ctx, ITEM)
    assert [plan.target for plan in plans] == ["poster"]
    assert plans[0].inputs["art"] == f"file:{custom}"


def test_apple_art_that_stops_loading_is_dropped_for_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple, "find", lambda kind, details, region: APPLE_URL)
    ctx = context([ref("/english.jpg", "en")])
    ctx.apple_region = "CA"
    plan = pipeline.movie(ctx, ITEM)[0]
    assert plan.inputs["art"] == APPLE_URL

    def fetch_or_fail(path: str) -> Image.Image:
        if path == APPLE_URL:
            raise posteryard_http.HttpError(503, path)
        return fetch(path)

    ctx.fetch = fetch_or_fail
    with pytest.raises(posteryard_http.HttpError):
        plan.draw()
    assert ctx.retry_without_apple()
    assert not ctx.retry_without_apple()
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == "/backdrop.jpg"
    ctx.apple_down_until = 0.0
    assert pipeline.movie(ctx, ITEM)[0].inputs["art"] == APPLE_URL


def test_titles_drawn_in_a_fallback_font_name_it(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context([ref("/textless.jpg", None)])
    monkeypatch.setattr(ctx, "logo", lambda title, dark=False: None)
    latin = pipeline.movie(ctx, ITEM)[0].inputs
    korean = pipeline.movie(ctx, {**ITEM, "Guid": [{"id": "tmdb://43"}], "title": "기생충"})[0].inputs
    assert "font" not in latin
    assert korean["font"] == "Pretendard"


def test_the_title_cache_is_written_under_the_lock() -> None:
    ctx = context([ref("/textless.jpg", None)])
    with ctx._lock:
        writer = threading.Thread(target=ctx.title, args=("tv", 42, "Example Show"))
        writer.start()
        writer.join(0.5)
        assert writer.is_alive()
    writer.join(5)
    assert ("tv", 42) in ctx.titles


def test_a_design_change_measures_the_art_again(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context([ref("/textless.jpg", None)])
    measured: list[str] = []
    real = designs.fade_strength

    def fade_strength(*args: Any) -> float:
        measured.append("fade")
        return real(*args)

    monkeypatch.setattr(designs, "fade_strength", fade_strength)
    pipeline.movie(ctx, ITEM)
    pipeline.movie(ctx, ITEM)
    assert measured == ["fade"]
    monkeypatch.setattr(pipeline, "MEASURED", "measured:next:")
    pipeline.movie(ctx, ITEM)
    assert measured == ["fade", "fade"]


class RecordingChoices(MemoryChoices):
    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str] = []

    def put_choice(self, key: str, value: Mapping[str, Any]) -> None:
        self.keys.append(key)
        super().put_choice(key, value)


def test_start_up_cleaning_keeps_every_choice_in_use() -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.choices = choices = RecordingChoices()
    pipeline.movie(ctx, ITEM)
    families = pipeline.CHOICE_FAMILIES.items()
    dropped = [k for k in choices.keys for f, keep in families if k.startswith(f) and not k.startswith(keep)]
    assert choices.keys and dropped == []
