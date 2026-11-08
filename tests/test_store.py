import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from posteryard.store import GUIDE_TABLE, MIGRATIONS, SCHEMA_1, Status, Store, StoreError


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


def test_a_new_database_gets_the_latest_schema_version(tmp_path: Path) -> None:
    Store(tmp_path / "state.db").close()
    with closing(sqlite3.connect(tmp_path / "state.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_a_database_from_before_versioning_keeps_its_rows(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "state.db")) as db:
        db.executescript(SCHEMA_1)
        db.execute("INSERT INTO meta(key, value) VALUES('sweep_cursor', '42')")
        db.commit()
    store = Store(tmp_path / "state.db")
    assert store.meta("sweep_cursor") == "42"


def test_uploads_recorded_before_the_uploads_list_are_known(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "state.db")) as db:
        db.executescript(SCHEMA_1 + GUIDE_TABLE)
        db.execute("INSERT INTO images(rating_key, target, image_key) VALUES('1', 'poster', 'upload-1')")
        db.execute("PRAGMA user_version=2")
        db.commit()
    store = Store(tmp_path / "state.db")
    store.forget("1")
    assert store.is_upload("1", "poster", "upload-1")
    assert not store.is_upload("1", "poster", "upload-2")
    store.uploaded("1", "poster", "One", "fp", "upload-2")
    store.drop_uploads("1")
    assert not store.is_upload("1", "poster", "upload-2")


def test_label_sets_report_gained_and_lost_keys(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    assert store.relabel("posteryard-custom", {"1", "2"}) == ({"1", "2"}, set())
    assert store.relabel("posteryard-custom", {"2", "3"}) == ({"3"}, {"1"})
    store.unlabel("posteryard-custom", "3")
    store.unlabel("posteryard-custom", "9")
    assert store.relabel("posteryard-custom", {"2", "3"}) == ({"3"}, set())


def test_a_database_from_a_newer_release_is_refused(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "state.db")) as db:
        db.execute(f"PRAGMA user_version={len(MIGRATIONS) + 1}")
    with pytest.raises(StoreError, match="newer"):
        Store(tmp_path / "state.db")


def test_replaced_and_reset_custom_art_files_are_deleted(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    folder = tmp_path / "custom"
    folder.mkdir()
    first, second = folder / "1-a.jpg", folder / "1-b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    store.set_custom("1", str(first), "command")
    store.set_custom("1", str(first), "command")
    assert first.exists()
    store.set_custom("1", str(second), "command")
    assert not first.exists() and second.exists()
    store.reset_override("1")
    assert not second.exists()
    outside = tmp_path / "mine.jpg"
    outside.write_bytes(b"c")
    store.set_custom("2", str(outside), "command")
    store.reset_override("2")
    assert outside.exists()


def test_a_folder_it_cannot_write_is_refused(tmp_path: Path) -> None:
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(StoreError, match=f"{tmp_path} is not writable by user {os.getuid()}:{os.getgid()}"):
            Store(tmp_path / "state.db")
    finally:
        tmp_path.chmod(0o700)


def test_a_database_file_it_cannot_write_is_refused(tmp_path: Path) -> None:
    Store(tmp_path / "state.db").close()
    (tmp_path / "state.db").chmod(0o444)
    with pytest.raises(StoreError, match=r"state\.db is not writable by user"):
        Store(tmp_path / "state.db")


def test_a_database_that_cannot_be_opened_is_refused(tmp_path: Path) -> None:
    (tmp_path / "state.db").mkdir()
    with pytest.raises(StoreError, match=r"state\.db could not be opened"):
        Store(tmp_path / "state.db")


def test_old_versions_of_cached_choices_are_deleted(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    keys = ["textless1:movie:1", "textless2:movie:1", "textless-all1:tv:2", "textless-all2:tv:2", "apple:tv:2:CA"]
    for key in keys:
        store.put_choice(key, {})
    assert store.drop_choices("textless", ("textless2:", "textless-all2:")) == 2
    assert [key for key in keys if store.get_choice(key) is not None] == [
        "textless2:movie:1", "textless-all2:tv:2", "apple:tv:2:CA",
    ]  # fmt: skip
    store.put_choice("titled:movie:1", {})
    assert store.drop_choices("titled:", ()) == 1


def test_guide_seen_writes_all_rows_in_one_transaction(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    statements: list[str] = []
    store._db.set_trace_callback(statements.append)
    store.guide_seen(["a", "b", "c"], 5)
    store._db.set_trace_callback(None)
    assert [s for s in statements if s in ("BEGIN", "COMMIT")] == ["BEGIN", "COMMIT"]
