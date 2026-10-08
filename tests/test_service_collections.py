from datetime import date
from typing import Any

import pytest

from posteryard import http, pipeline, service_collections
from posteryard.quality import QualityMinimums
from posteryard.server import Item
from posteryard.settings import Settings
from posteryard.sources import Sources, Title
from posteryard.tmdb import Kind
from tests.fakes import FakeServer, FakeTmdb

SHOWS = {
    "1": "netflix", "2": "netflix", "3": "netflix",
    "4": "hulu", "5": None, "6": "appletv", "7": "appletv", "8": "appletv",
}  # fmt: skip


class FakePlex(FakeServer):
    def __init__(self, ours: list[Item]) -> None:
        self.ours = ours
        self.members: dict[str, set[str]] = {"50": {"6", "9"}}
        self.calls: list[tuple[str, ...]] = []

    def section_items(self, section_key: str, kind: str, **filters: Any) -> list[Item]:
        if kind == "show":
            return [{"ratingKey": k, "title": f"Show {k}", "Guid": [{"id": f"tmdb://{k}"}]} for k in SHOWS]
        assert filters == {"label": service_collections.MANAGED_LABEL}
        return self.ours

    def collection_children(self, rating_key: str) -> list[Item]:
        return [{"ratingKey": k} for k in sorted(self.members.get(rating_key, set()))]

    def create_collection(self, section_key: str, kind: str, title: str, rating_keys: list[str]) -> str:
        self.calls.append(("create", title, *rating_keys))
        return "60"

    def set_label(self, section_key: str, kind: str, rating_key: str, label: str) -> None:
        self.calls.append(("label", rating_key, label))

    def add_to_collection(self, rating_key: str, members: list[str]) -> None:
        self.calls.append(("add", rating_key, *members))

    def remove_from_collection(self, rating_key: str, member: str) -> None:
        self.calls.append(("remove", rating_key, member))

    def delete_collection(self, rating_key: str) -> None:
        self.calls.append(("delete", rating_key))


class FailingLabel(FakePlex):
    def set_label(self, section_key: str, kind: str, rating_key: str, label: str) -> None:
        raise http.RequestError("HTTP 500", "http://plex.example:32400/library/sections/4/all")


def test_a_collection_that_cannot_be_labelled_is_removed() -> None:
    plex = FailingLabel([])
    with pytest.raises(http.RequestError):
        service_collections.sync(plex, context(), "4")
    assert ("delete", "60") in plex.calls


def test_a_show_whose_lookup_fails_keeps_its_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    plex = FakePlex([{"ratingKey": "50", "title": "Apple TV"}])
    ctx = context()
    real = ctx.sources.title

    def title(kind: Kind, tid: int, name: str) -> Title:
        if tid == 6:
            raise http.HttpError(404, "https://api.themoviedb.org/3/tv/6")
        return real(kind, tid, name)

    monkeypatch.setattr(ctx.sources, "title", title)
    plex.members["50"] = {"6", "7", "8"}
    assert "50" in service_collections.sync(plex, ctx, "4")
    assert not any(c[0] in ("remove", "delete") for c in plex.calls)


class UnmatchedShow(FakePlex):
    def section_items(self, section_key: str, kind: str, **filters: Any) -> list[Item]:
        items = super().section_items(section_key, kind, **filters)
        return [{**i, "Guid": []} if i["ratingKey"] == "8" else i for i in items]


def test_an_unmatched_show_keeps_its_membership() -> None:
    plex = UnmatchedShow([{"ratingKey": "50", "title": "Apple TV"}])
    plex.members["50"] = {"6", "7", "8"}
    assert "50" in service_collections.sync(plex, context(), "4")
    assert not any(c[0] in ("remove", "delete") for c in plex.calls)


def context() -> pipeline.Context:
    settings = Settings(QualityMinimums(), ("US",), {}, date(2026, 10, 3))
    ctx = pipeline.Context(settings, Sources(FakeTmdb(), settings))
    for key, service in SHOWS.items():
        ctx.sources.titles[("tv", int(key))] = (float("inf"), Title("tv", int(key), f"Show {key}", [], service))
    return ctx


def test_sync_creates_fills_and_removes_only_its_own_collections() -> None:
    ours: list[Item] = [
        {"ratingKey": "50", "title": "Apple TV"},
        {"ratingKey": "51", "title": "Hulu"},
        {"ratingKey": "52", "title": "Peacock"},
    ]
    plex = FakePlex(ours)
    kept = service_collections.sync(plex, context(), "4")
    assert kept == ["50", "60"]
    assert ("add", "50", "7", "8") in plex.calls
    assert ("remove", "50", "9") in plex.calls
    assert ("create", "Netflix", "1", "2", "3") in plex.calls
    assert ("label", "60", service_collections.MANAGED_LABEL) in plex.calls
    assert ("delete", "51") in plex.calls
    assert ("delete", "52") in plex.calls
