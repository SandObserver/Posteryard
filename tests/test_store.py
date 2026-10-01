from pathlib import Path

from posteryard.store import Status, Store


def test_retry_backoff_doubles_and_caps(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.failed("1", "item", "One", "boom")
    at = store.get("1", "item")
    assert at is not None and at.status == Status.FAILED
    assert store.retry_due(at.updated_at + 899) == []
    assert store.retry_due(at.updated_at + 900) == ["1"]
    for _ in range(9):
        store.failed("1", "item", "One", "boom")
    record = store.get("1", "item")
    assert record is not None and record.failures == 10
    assert store.retry_due(record.updated_at + 12 * 3600) == ["1"]


def test_targets_are_tracked_separately(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.uploaded("1", "poster", "One", "fp1", "img1")
    store.previewed("1", "art", "One", "fp2")
    assert store.counts() == {"uploaded": 1, "preview": 1}
    store.forget_target("1", "art")
    assert store.get("1", "art") is None
    store.forget("1")
    assert store.keys() == set()


def test_choices_and_meta_survive_a_reopen(tmp_path: Path) -> None:
    Store(tmp_path / "state.db").put_choice("titled:movie:1", {"candidates": ["/a.jpg"], "path": "/a.jpg"})
    Store(tmp_path / "state.db").set_meta("sweep_cursor", "42")
    reopened = Store(tmp_path / "state.db")
    assert reopened.get_choice("titled:movie:1") == {"candidates": ["/a.jpg"], "path": "/a.jpg"}
    assert reopened.meta("sweep_cursor") == "42"
