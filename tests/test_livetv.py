import hashlib
import io
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from posteryard import http, pipeline
from posteryard.livetv import (
    FORGET_SECONDS,
    LeftAloneError,
    LiveTv,
    fetch_image,
    guide_shows,
    norm,
    shown,
    without_notes,
)
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


def airing(title: str, key: str, *, channel: str = "News 24", thumb: str = "", movie: bool = False) -> Item:
    media = {"channelCallSign": channel, "channelThumb": LOGO}
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

    def _guide_id(self, provider: str, guid: str) -> str | None:
        return next((k for k, v in self.mixed.items() if f"g/{v}" == guid and k.isdigit()), None)

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


def test_a_shared_picture_stays_shared_after_one_show_is_uploaded(tmp_path: Path) -> None:
    shared = "https://img.example/shared.jpg"
    live, _, _ = build(tmp_path, [airing("Morning", "a", thumb=shared), airing("Evening", "b", thumb=shared)])
    live.run(now=1000)
    assert live.store.guide(f"{GUIDE}/a").picture == shared  # type: ignore[union-attr]
    shows = guide_shows(
        GUIDE,
        [airing("Morning", "a", thumb="/library/metadata/1/thumb/2"), airing("Evening", "b", thumb=shared)],
        live._remembered_picture,
    )
    assert [s.picture for s in shows] == ["", ""]


def test_one_show_on_two_channels_keeps_its_own_picture() -> None:
    own = "https://img.example/talk.jpg"
    shows = guide_shows(
        GUIDE,
        [airing("Talk", "one/talk", channel="One", thumb=own), airing("Talk", "two/talk", channel="Two", thumb=own)],
    )
    assert [s.picture for s in shows] == [own, own]


@pytest.mark.parametrize(
    ("title", "plain"),
    [
        ("Hercai (Tekrar)", "Hercai"),
        ("Inception (1080p) [HD] ", "Inception"),
        ("A (b (c)", "A"),
        ("Show (part] ", "Show"),
        ("(Live)", ""),
        ("News (live) today", "News (live) today"),
        ("Odd ) ending )", "Odd ) ending )"),
        ("Trailing space  ", "Trailing space  "),
    ],
)
def test_trailing_notes_are_cut(title: str, plain: str) -> None:
    assert without_notes(title) == plain


def test_cutting_notes_is_fast_on_a_hostile_title() -> None:
    started = time.monotonic()
    without_notes("(]" * 32000)
    without_notes("(x) " * 16000)
    assert time.monotonic() - started < 1


def test_logged_guide_addresses_hide_credentials(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    picture = "https://guide.example/p.jpg?password=hunter2"
    live, _, _ = build(tmp_path, [airing("Talk", "a", thumb=picture)])
    assert live.run()["uploaded"] == 1
    assert "picture from the guide" in str([r.__dict__ for r in caplog.records])
    assert all("hunter2" not in str(record.__dict__) for record in caplog.records)


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


def test_a_block_named_after_its_channel_gets_the_title_card(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    show = guide_shows(GUIDE, [airing("News 24", "a", thumb="https://img.example/own.jpg")])[0]
    plan = live.plan(show, fetch)
    assert plan.inputs["design"] == "live-frosted"
    assert "picture" not in plan.inputs
    assert tmdb.searches == []
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
    assert titled.inputs["design"] == "live-frosted"
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


def test_a_channel_without_logo_still_gets_cards(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Example Movie"] = [{"id": 5, "name": "Example Movie"}]
    for title in ("Bulletin", "News 24", "Example Movie"):
        show = replace(guide_shows(GUIDE, [airing(title, "c")])[0], logo="")
        plan = live.plan(show, lambda url: pytest.fail(f"fetched {url}"))
        assert plan.draw().size == (1000, 1500)
        assert plan.inputs["design"] in ("live-title", "tile")


def test_a_note_after_the_guide_title_is_left_out_of_the_tmdb_poster(tmp_path: Path) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.results["Example Movie"] = [{"id": 5, "name": "Example Movie"}]
    plan = live.plan(guide_shows(GUIDE, [airing("Example Movie (R)", "a")])[0], fetch)
    assert plan.inputs["design"] == "tile"
    assert plan.inputs["logo"] == "/logo.png"
    assert "logo_languages" not in plan.inputs
    assert plan.name == "Example Movie"


def test_guide_pictures_are_not_cached_but_channel_logos_are(tmp_path: Path) -> None:
    fetched: list[str] = []

    def counting(url: str) -> Image.Image:
        fetched.append(url)
        return fetch(url)

    shows = [airing("Talk", "a", thumb="https://img.example/talk.jpg"), airing("Bulletin", "b"), airing("Night", "n")]
    live, _, _ = build(tmp_path, shows, dry_run=True)
    live._fetch = counting
    live.run()
    assert fetched.count(LOGO) == 1
    assert fetched.count("https://img.example/talk.jpg") == 1


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


def test_the_run_summary_has_logfmt_field_names(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    live.run(now=1000)
    for image in plex.posters["a"]:
        image["selected"] = image["ratingKey"] == "guide-link"
    live.run(now=2000)
    [summary] = [r for r in caplog.records if r.getMessage() == "live TV posters checked" and r.created][-1:]
    assert getattr(summary, "selected_again", 0) == 1


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


def test_dry_run_keeps_the_record_of_an_upload(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    live.run(now=1000)
    live.dry_run = True
    plex.airings = [airing("Bulletin", "a", thumb="https://img.example/new.jpg")]
    assert live.run(now=2000)["preview"] == 1
    live.dry_run = False
    assert live.run(now=3000)["uploaded"] == 1


def test_a_poster_the_server_deleted_makes_the_guide_due(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a"), airing("Night", "b")])
    live.run(now=1000)
    assert not live.due(now=2000)
    plex.posters["b"] = [{"ratingKey": "guide-link", "selected": True}]
    assert live.due(now=2000)
    assert live.run(now=2000)["uploaded"] == 1
    assert plex.uploads == ["a", "b", "b"]
    assert not live.due(now=3000)

    def gone(provider: str, key: str) -> list[Item]:
        raise http.HttpError(404, "http://plex.example")

    monkeypatch.setattr(plex, "guide_images", gone)
    assert not live.due(now=3000)


def test_only_shows_from_the_last_run_are_checked_for_deleted_posters(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a"), airing("Night", "b")])
    live.run(now=1000)
    plex.airings = [airing("Bulletin", "a")]
    plex.refreshed = 2
    live.run(now=2000)
    plex.posters["b"] = []
    assert not live.due(now=3000)


@pytest.mark.parametrize("outcome", ["left alone", "failed", "skipped", "manual"])
def test_a_deleted_poster_the_run_cannot_restore_stops_making_the_guide_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    live.run(now=1000)
    plex.posters["a"] = [{"ratingKey": "guide-link", "selected": True}]
    if outcome == "left alone":
        monkeypatch.setattr(live, "plan", lambda show, fetch: (_ for _ in ()).throw(LeftAloneError(show.title)))
    elif outcome == "failed":
        monkeypatch.setattr(live, "plan", lambda show, fetch: (_ for _ in ()).throw(ValueError("unreadable")))
    elif outcome == "skipped":
        monkeypatch.setattr(plex, "guide_target", lambda provider, key, guid: None)
    else:
        plex.posters["a"] = [{"ratingKey": "upload://posters/by-hand", "selected": True}]
    assert live.due(now=2000)
    assert live.run(now=2000)[outcome] == 1
    assert not live.due(now=3000)


def test_dry_run_does_not_check_for_deleted_posters(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")])
    live.run(now=1000)
    live.dry_run = True
    live.run(now=2000)
    plex.posters["a"] = [{"ratingKey": "guide-link", "selected": True}]
    assert not live.due(now=3000)


def test_each_check_asks_for_a_few_posters_and_all_are_asked_in_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, plex, _ = build(tmp_path, [airing(f"Show {n}", str(n)) for n in range(7)])
    live.run(now=1000)
    asked: list[str] = []
    images = plex.guide_images

    def counted(provider: str, key: str) -> list[Item]:
        asked.append(key)
        return images(provider, key)

    monkeypatch.setattr(plex, "guide_images", counted)
    for _ in range(3):
        assert not live.due(now=2000)
    assert len(asked) == 9
    assert set(asked) == {str(n) for n in range(7)}


def test_turning_dry_run_off_makes_the_guide_due(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")], dry_run=True)
    assert live.run(now=1000)["preview"] == 1
    assert not live.due(now=2000)
    live.dry_run = False
    assert live.due(now=2000)
    assert live.run(now=2000)["uploaded"] == 1
    assert plex.uploads == ["a"]


def test_dry_run_saves_previews_once(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a")], dry_run=True)
    assert live.run()["preview"] == 1
    assert live.run()["unchanged"] == 1
    assert len(list((tmp_path / "previews").glob("livetv-bulletin-*.jpg"))) == 1
    assert plex.uploads == []


def test_unreadable_logos_and_pictures_are_left_out(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def broken(url: str) -> Image.Image:
        raise http.RequestError("refused", url)

    shows = [airing("Bulletin", "a"), airing("Talk", "b", thumb="https://img.example/talk.jpg"), airing("News 24", "n")]
    live, _, _ = build(tmp_path, shows)
    calls: list[str] = []

    def counted(url: str) -> Image.Image:
        calls.append(url)
        return broken(url)

    live._fetch = counted
    assert live.run()["uploaded"] == 3
    assert caplog.text.count("could not be read") == 2
    assert calls.count(LOGO) == 1
    plan = live.plan(guide_shows(GUIDE, [airing("Bulletin", "a")])[0], broken)
    assert plan.inputs["logo"] == ""


def test_run_counts_failures_and_stops_when_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live, plex, _ = build(tmp_path, [airing("Bulletin", "a"), airing("Night", "b")])

    def refused(provider: str, key: str, jpeg: bytes) -> str:
        raise http.RequestError("refused", "http://plex.example")

    monkeypatch.setattr(plex, "guide_upload", refused)
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


def test_the_channel_logo_sits_where_a_streaming_mark_does() -> None:
    poster = cards.with_channel(Image.new("RGB", (1000, 1500), (0, 0, 0)), logo())
    assert poster.getpixel((60, 60)) == (220, 40, 40)
    assert poster.getpixel((500, 750)) == (0, 0, 0)


def test_a_picture_too_thin_for_a_poster_gets_the_title_card(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    live, _, _ = build(tmp_path, [])
    strip = Image.effect_noise((1920, 10), 60).convert("RGB")
    show = guide_shows(GUIDE, [airing("Talk", "b", thumb="https://img.example/strip.jpg")])[0]
    live._fetch = lambda url: strip if url.endswith("strip.jpg") else fetch(url)
    plan = live.plan(show, fetch)
    assert plan.inputs["design"] == "live-frosted"
    assert any("too thin for a poster" in str(getattr(r, "reason", "")) for r in caplog.records)
    assert cards.guide_picture(Image.effect_noise((1920, 1080), 60)).size == (1920, 1080)


def test_trim_bars_cuts_flat_padding_only() -> None:
    padded = Image.new("RGB", (100, 150), (27, 46, 68))
    padded.paste(Image.effect_noise((100, 56), 60).convert("RGB"), (0, 47))
    assert cards.trim_bars(padded).size == (100, 56)
    assert cards.trim_bars(Image.new("RGB", (40, 40), (5, 5, 5))).size == (40, 40)


def test_a_guide_entry_plex_mixes_up_with_another_and_no_id_reaches_is_left_alone(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("atv", "a", channel="atv"), airing("ATV Ana Haber", "b", channel="atv")])
    plex.mixed["a"] = "b"
    assert dict(live.run()) == {"skipped": 1, "uploaded": 1}
    assert plex.uploads == ["b"]


def test_a_guide_entry_plex_mixes_up_with_another_gets_its_poster_by_numeric_id(tmp_path: Path) -> None:
    live, plex, _ = build(tmp_path, [airing("News", "a"), airing("News at Ten", "b")])
    plex.mixed |= {"a": "b", "7": "a"}
    assert dict(live.run(now=1000)) == {"uploaded": 2}
    assert sorted(plex.uploads) == ["7", "b"]
    assert live.store.guide(f"{GUIDE}/a").image_key == plex.posters["7"][1]["ratingKey"]  # type: ignore[union-attr]
    assert live.store.meta("livetv_confirmed") == f'["{GUIDE}/b"]'
    plex.refreshed = 2
    for images in plex.posters.values():
        images[0]["selected"], images[1]["selected"] = True, False
    assert dict(live.run(now=2000)) == {"selected again": 2}
    assert sorted(key for key, _ in plex.selects) == ["7", "b"]


def test_a_guide_entry_renumbered_before_the_upload_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live, plex, _ = build(tmp_path, [airing("News", "a"), airing("News at Ten", "b")])
    plex.mixed |= {"a": "b", "7": "a"}
    images = plex.guide_images

    def refresh(provider: str, key: str) -> list[Item]:
        if key == "7":
            plex.mixed |= {"7": "c", "8": "a"}
        return images(provider, key)

    monkeypatch.setattr(plex, "guide_images", refresh)
    assert dict(live.run(now=1000)) == {"skipped": 1, "uploaded": 1}
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


def test_a_guide_name_never_reaches_a_library_poster(tmp_path: Path) -> None:
    clean = pipeline.plan_tmdb(fake_context([ref("/poster.jpg", None)]), "movie", 5)[0]
    live, _, _ = build(tmp_path, [])
    pipeline.live_poster(live.ctx, "movie", 5, "guide/x", "Example Movie Special")
    library = pipeline.plan_tmdb(live.ctx, "movie", 5)[0]
    assert library.name == clean.name == "Example Movie"


def test_titles_are_drawn_without_emoji_and_matched_without_notes() -> None:
    assert shown("Fish Tank Live 🐠") == "Fish Tank Live"
    assert shown("خبر 📰 ویژه") == "خبر ویژه"
    assert shown("چشم‌انداز") == "چشم‌انداز"
    assert shown("🐠") == "🐠"
    assert norm("Inception (1080p) [HD]") == norm("inception")


def test_long_live_titles_may_take_three_lines() -> None:
    title = "A Very Long Show Title That Goes On And On And Keeps Going Past Every Reasonable Length"
    two, three = designs.text_logo(title), designs.text_logo(title, rows=3)
    assert three.height / three.width > two.height / two.width


def test_a_plex_show_on_several_channels_gets_no_channel_logo() -> None:
    shows = guide_shows(
        GUIDE, [airing("Bulletin", "a"), airing("Bulletin", "a", channel="Other"), airing("Night", "b")]
    )
    assert {s.title: s.logo for s in shows} == {"Bulletin": "", "Night": LOGO}


def test_plex_own_guide_gets_only_tmdb_posters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    own = "tv.plex.providers.epg.cloud:20"
    live, plex, tmdb = build(tmp_path, [])
    tmdb.results["Example Movie"] = [{"id": 5, "name": "Example Movie"}]
    plex.airings = [airing("Example Movie", "a", thumb="https://img.example/a.jpg"), airing("Local News", "b"),
                    airing("News 24", "c")]  # fmt: skip
    monkeypatch.setattr(plex, "guides", lambda: [Guide(own, 1)])
    assert dict(live.run()) == {"uploaded": 1, "left alone": 2}
    assert plex.uploads == ["a"]


def test_a_tmdb_title_with_only_wide_art_is_treated_as_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, _, tmdb = build(tmp_path, [])
    tmdb.posters = []
    tmdb.results["Talk"] = [{"id": 5, "name": "Talk"}]
    monkeypatch.setattr(live.ctx.sources, "fetch", lambda path: Image.new("RGB", (1920, 1080), (40, 60, 80)))
    show = guide_shows(GUIDE, [airing("Talk", "b", thumb="https://img.example/talk.jpg")])[0]
    assert live.plan(show, fetch).inputs["design"] == "live-picture"
    own = replace(show, provider="tv.plex.providers.epg.cloud:20")
    with pytest.raises(LeftAloneError):
        live.plan(own, fetch)


def test_the_title_card_is_frosted_in_the_logo_colours_and_grey_without_a_logo() -> None:
    title = designs.text_logo("Bulletin", rows=3)
    away_from_the_corner = (cards.W - 60, round(cards.H * 0.6))
    red, green, blue = cards.title_card(logo(), title).getpixel(away_from_the_corner)  # type: ignore[misc]
    assert red > 2 * max(green, blue)
    grey = cards.title_card(None, title).getpixel(away_from_the_corner)
    assert max(grey) - min(grey) < 10  # type: ignore[arg-type]


def test_a_dark_logo_is_frosted_over_white_and_a_see_through_logo_is_refused() -> None:
    title = designs.text_logo("Bulletin", rows=3)
    navy = cards.logo_mark(logo((20, 30, 90)))
    assert cards.dark_logo(navy)
    assert not cards.dark_logo(cards.logo_mark(logo()))
    assert min(cards.title_card(navy, title).getpixel((60, 60))) > 120  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="transparent"):
        cards.logo_mark(Image.new("RGBA", (300, 120), (0, 0, 0, 0)))
    with pytest.raises(ValueError, match="transparent"):
        cards.logo_mark(Image.new("RGB", (1, 1), (200, 30, 30)))


@pytest.mark.parametrize(
    ("url", "untrusted"),
    [
        ("http://127.0.0.1:8096/Items/1/Images/Primary", False),
        ("http://127.0.0.1:8097/logo.png", True),
        ("https://127.0.0.1:8096/logo.png", True),
        ("https://logos.example/news.png", True),
    ],
)
def test_only_the_media_server_may_serve_guide_images_from_this_machine(
    monkeypatch: pytest.MonkeyPatch, url: str, untrusted: bool
) -> None:
    seen: dict[str, Any] = {}

    def request(method: str, address: str, **kwargs: Any) -> bytes:
        seen.update(kwargs)
        out = io.BytesIO()
        Image.new("RGB", (4, 4)).save(out, "PNG")
        return out.getvalue()

    monkeypatch.setattr(http, "request", request)
    fetch_image(url, server="http://127.0.0.1:8096")
    assert seen["untrusted"] is untrusted
