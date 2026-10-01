from datetime import date

from posteryard.maintainerr import action_days, days_left, label


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
