from pathlib import Path
from typing import Any

import pytest

from posteryard import cli, overrides, pipeline, why
from posteryard.artwork import rejection
from posteryard.ocr import TextLine
from posteryard.server import Item
from posteryard.store import Store
from tests.test_cli import run  # noqa: F401
from tests.test_pipeline import context, ref

MOVIE: Item = {"ratingKey": "1", "type": "movie", "title": "Example Movie", "Guid": [{"id": "tmdb://42"}]}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state.db")


def test_rejection_names_the_title_or_the_large_text() -> None:
    title = [TextLine("EXAMPLE MOVIE", 0.99, 0.02, 0.2)]
    tagline = [TextLine("ONE NIGHT ONLY", 0.99, 0.06, 0.6)]
    small = [TextLine("WORLD'S BEST BOSS", 0.99, 0.02, 0.2)]
    assert rejection(title, ["Example Movie"]) == 'shows the title: "EXAMPLE MOVIE"'
    assert rejection(tagline, ["Example Movie"]) == 'large text: "ONE NIGHT ONLY"'
    assert rejection(small, ["Example Movie"]) is None


def test_report_lists_candidates_until_the_art_in_use(store: Store) -> None:
    ctx = context([ref("/english.jpg", None), ref("/foreign.jpg", None), ref("/textless.jpg", None)])
    result = why.report(ctx, MOVIE, store)
    assert result.art == "/textless.jpg"
    assert result.note == "art from TMDB /textless.jpg"
    [(group, candidates)] = result.groups
    assert group == "TMDB posters"
    assert [(c.ref.path, c.reason) for c in candidates] == [
        ("/english.jpg", 'shows the title: "EXAMPLE MOVIE"'),
        ("/foreign.jpg", 'large text: "PELICULA DE EJEMPLO"'),
        ("/textless.jpg", None),
    ]
    assert result.unchecked == ["TMDB backdrops"]
    text = "\n".join(why.lines("Example Movie (2024, movie)", result))
    assert "  1. /english.jpg  2000x3000  rated 5.0 (10 votes)  shows the title" in text
    assert "  3. /textless.jpg  2000x3000  rated 5.0 (10 votes)  used" in text


def test_the_first_clean_candidate_is_the_art_the_service_uses(store: Store) -> None:
    ctx = context([ref("/english.jpg", None)])
    result = why.report(ctx, MOVIE, store)
    assert result.art == "/backdrop.jpg"
    assert [group for group, _ in result.groups] == ["TMDB posters", "TMDB backdrops"]
    first_clean = next(c.ref.path for _, cs in result.groups for c in cs if c.reason is None)
    assert first_clean == result.art


def test_art_next_shows_the_skipped_art_and_the_wider_pool(store: Store) -> None:
    ctx = context([ref("/textless.jpg", None)])
    ctx.overrides = lambda key: overrides.Override(skip=frozenset({"/textless.jpg"}))
    result = why.report(ctx, MOVIE, store)
    assert result.art == "/backdrop.jpg"
    assert "`art next` skipped 1 image(s). `art reset` returns to the first." in result.notices
    text = "\n".join(why.lines("Example Movie", result))
    assert "/textless.jpg  2000x3000  rated 5.0 (10 votes)  skipped by art next" in text


def test_custom_art_lists_no_candidates(store: Store, tmp_path: Path) -> None:
    ctx = context([ref("/textless.jpg", None)])
    custom = tmp_path / "custom.jpg"
    ctx.overrides = lambda key: overrides.Override(custom=str(custom), source="label")
    pipeline_load = ctx.load
    ctx.load = lambda path: pipeline_load("/textless.jpg") if path.startswith("file:") else pipeline_load(path)  # type: ignore[method-assign]
    result = why.report(ctx, MOVIE, store)
    assert result.notices == ["Custom art from label. `art reset` returns to automatic art."]
    assert result.groups == []


def test_hand_changed_and_ignored_posters_are_named(store: Store) -> None:
    store.manual("1", "poster", "Example Movie")
    item: Item = {**MOVIE, "Label": [{"tag": overrides.IGNORE_LABEL}]}
    result = why.report(context([ref("/textless.jpg", None)]), item, store)
    assert result.notices == [
        "posteryard-ignore is set: Posteryard leaves this poster alone.",
        "The poster was changed by hand. Posteryard leaves it alone until `forget`.",
    ]


def test_choices_are_read_from_the_store_and_never_written(store: Store) -> None:
    store.put_choice("kept", {"path": "/a.jpg"})
    choices = why.ReadOnlyChoices(store)
    choices.put_choice("new", {"path": "/b.jpg"})
    assert choices.get_choice("kept") == {"path": "/a.jpg"}
    assert choices.get_choice("new") == {"path": "/b.jpg"}
    assert store.get_choice("new") is None


def test_why_command_prints_the_report(run: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:  # noqa: F811
    seen: list[str] = []

    def report(ctx: pipeline.Context, item: Item, store: Store) -> why.Report:
        seen.append(str(item["ratingKey"]))
        return why.Report("/a.jpg", "art from TMDB /a.jpg", None)

    monkeypatch.setattr(cli, "Worker", lambda *args: type("W", (), {"ctx": context([])})())
    monkeypatch.setattr(why, "report", report)
    assert run(["why", "Blade", "Runner", "2049"]) == 0
    assert seen == ["301"]
    out = capsys.readouterr().out
    assert out.startswith("Blade Runner 2049 (2017, movie)\n  Uses: art from TMDB /a.jpg\n  Logo: the name in text")
