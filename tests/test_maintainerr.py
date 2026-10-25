from datetime import UTC, date, timedelta, timezone
from typing import Any

import pytest

from posteryard import http
from posteryard.maintainerr import Maintainerr, action_days, days_left, label


def test_earliest_active_collection_wins() -> None:
    collections = [
        {
            "isActive": True,
            "deleteAfterDays": 30,
            "media": [{"mediaServerId": "10", "addDate": "2026-09-01T00:00:00.000Z"}],
        },
        {"isActive": True, "deleteAfterDays": 10, "media": [{"mediaServerId": "10", "addDate": "2026-09-01 08:00:00"}]},
        {"isActive": False, "deleteAfterDays": 1, "media": [{"mediaServerId": "11", "addDate": "2026-09-01"}]},
    ]
    assert action_days(collections) == {"10": date(2026, 9, 11)}


def test_days_left_and_label() -> None:
    today = date(2026, 10, 1)
    assert days_left(date(2026, 10, 13), today) == 12
    assert days_left(date(2026, 9, 30), today) is None
    assert days_left(None, today) is None
    assert [label(0), label(1), label(12)] == ["LEAVING TODAY", "LEAVING TOMORROW", "LEAVING IN 12 DAYS"]


def test_unexpected_answers_are_rejected_or_skipped() -> None:
    with pytest.raises(ValueError, match="list"):
        action_days({"error": "Unauthorized"})
    collections = [
        "not a collection",
        {"isActive": True, "deleteAfterDays": 3, "media": {"10": "x"}},
        {"isActive": True, "deleteAfterDays": 10**9, "media": [{"mediaServerId": "11", "addDate": "2026-09-01"}]},
        {"isActive": True, "deleteAfterDays": 3, "media": ["x", {"mediaServerId": "12", "addDate": "2026-09-01"}]},
    ]
    assert action_days(collections) == {"12": date(2026, 9, 4)}


def test_add_date_counts_in_the_local_time_zone() -> None:
    media = [{"mediaServerId": "10", "addDate": "2026-09-02T02:00:00.000Z"}]
    collections = [{"isActive": True, "deleteAfterDays": 7, "media": media}]
    assert action_days(collections, timezone(timedelta(hours=-4))) == {"10": date(2026, 9, 8)}
    assert action_days(collections, UTC) == {"10": date(2026, 9, 9)}


def test_a_bad_collection_or_title_is_skipped_and_the_rest_kept(caplog: pytest.LogCaptureFixture) -> None:
    good = {"mediaServerId": "10", "addDate": "2026-09-01"}
    collections = [
        {"isActive": True, "deleteAfterDays": 3, "media": [good, {"mediaServerId": "11", "addDate": "not a date"}]},
        *(
            {
                "isActive": True,
                "title": "Odd",
                "deleteAfterDays": days,
                "media": [{"mediaServerId": "12", "addDate": "2026-09-01"}],
            }
            for days in ("abc", {"value": 30}, [30], float("nan"))
        ),
    ]
    assert action_days(collections) == {"10": date(2026, 9, 4)}
    assert sum("delete days are not a number" in r.message for r in caplog.records) == 4
    assert sum("add date is not a date" in r.message for r in caplog.records) == 1


def test_every_title_is_read_not_only_the_list_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    def row(key: str, added: str) -> dict[str, Any]:
        return {"collectionId": 1, "mediaServerId": key, "addDate": added, "isManual": False}

    routes: dict[str, Any] = {
        "/api/collections": [
            {"id": 1, "isActive": True, "deleteAfterDays": 15, "mediaCount": 3, "media": [row("21077", "2026-10-24")]},
            {"id": 2, "isActive": True, "deleteAfterDays": 15, "mediaCount": 0, "media": []},
            {"id": 3, "isActive": False, "deleteAfterDays": 15, "media": [row("30", "2026-10-01")]},
        ],
        "/api/collections/media/?collectionId=1": [
            row("21077", "2026-10-24T04:00:00.000Z"),
            row("20866", "2026-10-13T04:00:00.000Z"),
            row("20760", "2026-10-12T04:00:00.000Z"),
        ],
        "/api/collections/media/?collectionId=2": [],
    }
    seen: list[str] = []

    def get_json(url: str, **kwargs: Any) -> Any:
        path = url.removeprefix("http://maintainerr:6246")
        seen.append(path)
        return routes[path]

    monkeypatch.setattr(http, "get_json", get_json)
    assert Maintainerr("http://maintainerr:6246").action_days() == {
        "21077": date(2026, 11, 8),
        "20866": date(2026, 10, 28),
        "20760": date(2026, 10, 27),
    }
    assert seen == list(routes)


def test_a_failed_media_read_fails_the_whole_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_json(url: str, **kwargs: Any) -> Any:
        if url.endswith("/api/collections"):
            return [{"id": 1, "isActive": True, "deleteAfterDays": 15, "media": []}]
        raise http.HttpError(500, url)

    monkeypatch.setattr(http, "get_json", get_json)
    with pytest.raises(http.HttpError):
        Maintainerr("http://maintainerr:6246").action_days()
