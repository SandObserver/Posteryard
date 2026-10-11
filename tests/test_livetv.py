import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from posteryard import http, pipeline
from posteryard.livetv import FORGET_SECONDS, LiveTv, guide_shows, norm
from posteryard.plex import Plex
from posteryard.render import designs
from posteryard.render import livetv as cards
from posteryard.server import Guide, Item
from posteryard.store import Store
from posteryard.tmdb import Details, ImageRef, Kind
from tests.fakes import FakeTmdb, ref
from tests.fakes import context as fake_context

GUIDE = "tv.plex.providers.epg.xmltv:15"
LOGO = "https://logos.example/news.png"


def airing(  # noqa: PLR0913
    title: str, key: str, *, channel: str = "News 24", thumb: str = "", movie: bool = False, length: int = 0
) -> Item:
    media = {"channelCallSign": channel, "channelThumb": LOGO, "beginsAt": 1000, "endsAt": 1000 + length}
    if movie:
        return {"type": "movie", "ratingKey": key, "title": title, "thumb": thumb, "guid": f"g/{key}", "Media": [media]}
    return {  # type: ignore[typeddict-unknown-key]
        "type": "episode", "grandparentRatingKey": key, "grandparentTitle": title, "grandparentThumb": thumb,
        "grandparentGuid": f"g/{key}", "Media": [media],
    }  # fmt: skip


class GuideTmdb(FakeTmdb):
    def __init__(self) -> None:
        super().__init__([ref("/poster.jpg", None)])
        self.results: dict[str, list[dict[str, Any]]] = {}
        self.searches: list[str] = []
        self.names = {"title": "Example Movie", "original_title": "Ejemplo"}

    def search(self, kind: Kind, query: str) -> list[dict[str, Any]]:
        self.searches.append(query)
        return self.results.get(query, [])

    def language_logos(self, kind: Kind, tmdb_id: int, languages: Sequence[str]) -> list[ImageRef]:
        return []

    def details(self, kind: Kind, tmdb_id: int) -> Details:
        return {**super().details(kind, tmdb_id), **self.names, "original_language": "es"}  # type: ignore[typeddict-item]


class GuidePlex(Plex):
    def __init__(self, airings: list[Item]) -> None:
        super().__init__("http://plex.example", "example-token")
        self.airings = airings
        self.refreshed = 1
        self.posters: dict[str, list[dict[str, Any]]] = {}
        self.uploads: list[str] = []
        self.selects: list[tuple[str, str]] = []
        self.mixed: dict[str, str] = {}

    def guide_item(self, provider: str, key: str) -> Item | None:
        return {"guid": f"g/{self.mixed.get(key, key)}", "title": key}

    def guides(self) -> list[Guide]:
        return [Guide(GUIDE, self.refreshed)]

    def guide_airings(self, provider: str, start: int, end: int) -> list[Item]:
        return self.airings

    def guide_images(self, provider: str, key: str) -> list[Item]:
        return self.posters.get(key, [])  # type: ignore[return-value]

    def guide_upload(self, provider: str, key: str, jpeg: bytes) -> str:
        image_key = "upload://posters/" + hashlib.sha1(jpeg).hexdigest()
        self.uploads.append(key)
        self.posters[key] = [{"ratingKey": "guide-link"}, {"ratingKey": image_key, "selected": True}]
        return image_key

    def guide_select(self, provider: str, key: str, image_key: str) -> None:
        self.selects.append((key, image_key))
        for image in self.posters[key]:
            image["selected"] = image["ratingKey"] == image_key


def logo(colour: tuple[int, int, int] = (220, 40, 40)) -> Image.Image:
    image = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 20, 280, 100), fill=(*colour, 255))
    return image


def fetch(url: str) -> Image.Image:
    if url == LOGO:
        return logo()
    return Image.new("RGB", (900, 500), (40, 90, 140))


def build(tmp_path: Path, airings: list[Item], *, dry_run: bool = False) -> tuple[LiveTv, GuidePlex, GuideTmdb]:
    plex = GuidePlex(airings)
    ctx = fake_context([ref("/poster.jpg", None)])
    tmdb = GuideTmdb()
    ctx.sources.tmdb = tmdb
    live = LiveTv(
        plex, Store(tmp_path / "state.db"), ctx, dry_run=dry_run, preview_dir=tmp_path / "previews", fetch=fetch
    )
    return live, plex, tmdb


def test_guide_shows_group_airings_and_drop_shared_pictures() -> None:
    shows = guide_shows(
        GUIDE,
        [
            airing("Morning", "a", thumb="https://img.example/shared.jpg"),
            airing("Morning", "a", thumb="https://img.example/shared.jpg"),
            airing("Evening", "b", thumb="https://img.example/shared.jpg"),
            airing("Night", "c", thumb="https://img.example/night.jpg"),
            airing("Late", "d", thumb="/server/path"),
            airing("Film", "m", thumb="https://img.example/film.jpg", movie=True),
            airing("", "e"),
        ],
    )
    pictures = {s.title: s.picture for s in shows}
    assert pictures == {
        "Morning": "", "Evening": "", "Night": "https://img.example/night.jpg", "Late": "",
        "Film": "https://img.example/film.jpg",
    }  # fmt: skip
    assert {s.title: s.kind for s in shows}["Film"] == "movie"


def test_norm_ignores_case_marks_and_a_trailing_note() -> None:
    assert norm("Kim Milyoner Olmak İster? (Tekrar)") == norm("kim milyoner olmak ister")


def test_match_takes_one_exact_title_and_caches_the_answer(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Daha 17"] = [{"id": 7, "name": "Torn Apart", "original_name": "Daha 17"}, {"id": 8, "name": "Daha"}]
    show = guide_shows(GUIDE, [airing("Daha 17", "a")])[0]
    assert live.match(show) == 7
    assert live.match(show) == 7
    assert tmdb.searches == ["Daha 17"]


def test_match_refuses_a_tie_but_takes_a_clear_favourite(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Bahar"] = [{"id": 1, "name": "Bahar", "vote_count": 10}, {"id": 2, "name": "Bahar", "vote_count": 6}]
    tmdb.results["Tuzak"] = [{"id": 3, "name": "Tuzak", "vote_count": 90}, {"id": 4, "name": "Tuzak", "vote_count": 2}]
    assert live.match(guide_shows(GUIDE, [airing("Bahar", "a")])[0]) is None
    assert live.match(guide_shows(GUIDE, [airing("Tuzak", "b")])[0]) == 3


def test_a_block_named_after_its_channel_gets_the_channel_card(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    show = guide_shows(GUIDE, [airing("News 24", "a", thumb="https://img.example/own.jpg")])[0]
    plan = live.plan(show, fetch)
    assert plan.inputs["design"] == "live-channel"
    assert plan.inputs["art"] is None
    assert tmdb.searches == []
    assert plan.draw().size == (1000, 1500)


def test_a_channel_block_shows_art_of_the_channel_show_with_most_airtime(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Long"] = [{"id": 5, "name": "Long"}]
    tmdb.results["Short"] = [{"id": 6, "name": "Short"}]
    live._shows = guide_shows(
        GUIDE,
        [
            airing("Short", "s", length=600),
            airing("Long", "l", length=600),
            airing("Long", "l", length=600),
            airing("Other channel", "o", channel="Elsewhere", length=9000),
            airing("News 24", "n", length=600),
        ],
    )
    plan = live.plan(live._shows[-1], fetch)
    assert plan.inputs["art"] == "/poster.jpg"
    assert tmdb.searches == ["Long"]
    assert plan.draw().size == (1000, 1500)


def test_plan_prefers_tmdb_then_the_guide_picture_then_a_title_card(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Example Movie"] = [{"id": 5, "name": "Example Movie"}]
    found = live.plan(guide_shows(GUIDE, [airing("Example Movie", "a")])[0], fetch)
    assert found.inputs["design"] == "tile"
    assert found.inputs["service"] is None
    assert found.inputs["label"] is None
    assert found.inputs["channel_logo"] == LOGO
    picture = live.plan(guide_shows(GUIDE, [airing("Talk", "b", thumb="https://img.example/talk.jpg")])[0], fetch)
    assert picture.inputs["design"] == "live-picture"
    titled = live.plan(guide_shows(GUIDE, [airing("Bulletin", "c")])[0], fetch)
    assert titled.inputs["design"] == "live-title"
    assert picture.draw().size == titled.draw().size == (1000, 1500)


def test_a_tmdb_match_without_art_falls_back_to_the_guide_picture(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.posters, tmdb.backdrops = [], []
    tmdb.results["Talk"] = [{"id": 5, "name": "Talk"}]
    show = guide_shows(GUIDE, [airing("Talk", "b", thumb="https://img.example/talk.jpg")])[0]
    assert live.plan(show, fetch).inputs["design"] == "live-picture"


def test_a_guide_using_the_original_title_gets_its_logo_language(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Ejemplo"] = [{"id": 5, "name": "Example Movie", "original_name": "Ejemplo"}]
    plan = live.plan(guide_shows(GUIDE, [airing("Ejemplo", "a")])[0], fetch)
    assert plan.inputs["logo_languages"] == ["es"]
    assert plan.inputs["text_logo"] == "Ejemplo"


def test_a_show_without_picture_or_logo_fails(tmp_path: Path) -> None:
    live, _, _ = build(tmp_path, [])
    show = guide_shows(GUIDE, [airing("Bulletin", "c")])[0]
    with pytest.raises(pipeline.NotFoundError, match="no picture or channel logo"):
        live.plan(show.__class__(**{**show.__dict__, "logo": ""}), fetch)


def test_run_uploads_once_then_selects_again_after_a_guide_refresh(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    assert live.due()
    assert live.run(now=1000)["uploaded"] == 1
    assert not live.due(now=2000)
    assert live.run(now=2000)["unchanged"] == 1
    for image in plex.posters["a"]:
        image["selected"] = image["ratingKey"] == "guide-link"
    plex.refreshed = 2
    assert live.due(now=3000)
    assert live.run(now=3000)["selected again"] == 1
    assert plex.uploads == ["a"]


def test_run_leaves_a_poster_someone_else_uploaded(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    plex.posters["a"] = [{"ratingKey": "upload://posters/by-hand", "selected": True}]
    assert live.run()["manual"] == 1
    assert plex.uploads == []


def test_run_uploads_again_when_the_design_changes(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    live.run(now=1000)
    plex.airings = [airing("Bulletin", "a", thumb="https://img.example/new.jpg")]
    assert live.run(now=2000)["uploaded"] == 1
    assert plex.uploads == ["a", "a"]


def test_dry_run_saves_previews_once(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")], dry_run=True)
    assert live.run()["preview"] == 1
    assert live.run()["unchanged"] == 1
    assert len(list((tmp_path / "previews").glob("livetv-bulletin-*.jpg"))) == 1
    assert plex.uploads == []


def test_run_counts_failures_and_stops_when_asked(tmp_path: Path) -> None:
    def broken(url: str) -> Image.Image:
        raise http.RequestError("refused", url)

    live, _, _ = build(tmp_path, [airing("Bulletin", "a"), airing("Night", "b")])
    live._fetch = broken
    assert live.run()["failed"] == 2
    assert sum(live.run(tick=lambda: False).values()) == 0


def test_records_of_shows_gone_from_the_guide_are_forgotten(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    live.run(now=1000)
    plex.airings = []
    live.run(now=1000 + FORGET_SECONDS + 1)
    assert live.store.guide(f"{GUIDE}/a") is None


def test_logo_mark_cuts_transparent_logos_and_rounds_opaque_ones() -> None:
    assert cards.logo_mark(logo()).size == (261, 81)
    photo = cards.logo_mark(Image.new("RGB", (200, 200), (10, 120, 10)))
    assert photo.getchannel("A").getpixel((0, 0)) == 0
    assert photo.getchannel("A").getpixel((100, 100)) == 255


def test_channel_cards_use_the_collection_band_with_or_without_art() -> None:
    plain = cards.channel_card(logo())
    assert plain.size == (1000, 1500)
    assert plain.getpixel((10, 10)) == cards.band(1500).getpixel((10, 10))
    with_art = cards.channel_card(logo(), Image.new("RGB", (600, 900), (200, 180, 40)))
    assert with_art.getpixel((500, 600)) != plain.getpixel((500, 600))
    split = round(designs.CHANNEL_SEAM * 1500)
    assert with_art.getpixel((10, 1400)) == cards.band(1500 - split).getpixel((10, 1400 - split))


def test_the_channel_logo_sits_where_a_streaming_mark_does() -> None:
    poster = cards.with_channel(Image.new("RGB", (1000, 1500), (0, 0, 0)), logo())
    assert poster.getpixel((60, 60)) == (220, 40, 40)
    assert poster.getpixel((500, 750)) == (0, 0, 0)


def test_trim_bars_cuts_flat_padding_only() -> None:
    padded = Image.new("RGB", (100, 150), (27, 46, 68))
    padded.paste(Image.effect_noise((100, 56), 60).convert("RGB"), (0, 47))
    assert cards.trim_bars(padded).size == (100, 56)
    assert cards.trim_bars(Image.new("RGB", (40, 40), (5, 5, 5))).size == (40, 40)


def test_a_guide_entry_plex_mixes_up_with_another_is_left_alone(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("atv", "a", channel="atv"), airing("ATV Ana Haber", "b", channel="atv")])
    plex.mixed["a"] = "b"
    assert dict(live.run()) == {"skipped": 1, "uploaded": 1}
    assert plex.uploads == ["b"]


def test_a_remembered_picture_is_used_only_while_plex_shows_the_upload(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Talk", "a", thumb="https://img.example/talk.jpg")])
    live.run(now=1000)
    plex.airings = [airing("Talk", "a", thumb="/library/metadata/9/thumb/1")]
    assert live.run(now=2000)["unchanged"] == 1
    plex.airings = [airing("Talk", "a")]
    plex.refreshed = 2
    assert live.run(now=3000)["uploaded"] == 1
    assert live.store.guide(f"{GUIDE}/a").picture == ""  # type: ignore[union-attr]
