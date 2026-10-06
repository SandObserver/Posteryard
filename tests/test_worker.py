import io
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from posteryard import config, http, pipeline
from posteryard.notify import Notifier
from posteryard.server import Item
from posteryard.store import Status, Store
from posteryard.worker import Outcome, Worker
from tests.test_pipeline import FakeTmdb, fetch, read, ref

MOVIE = {
    "ratingKey": "1", "type": "movie", "title": "Example Movie", "librarySectionTitle": "Movies",
    "librarySectionID": "3", "Guid": [{"id": "tmdb://42"}], "Media": [],
}  # fmt: skip


class FakePlex:
    name = "Plex"
    url = "http://plex.example:32400"

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {"1": dict(MOVIE)}
        self.selected_keys: dict[tuple[str, str], str] = {}
        self.uploads: list[tuple[str, str]] = []
        self.fail = False
        self.restored: list[tuple[str, str]] = []

    def item(self, key: str) -> dict[str, Any] | None:
        if self.fail:
            raise http.RequestError("ConnectionError", "http://plex.example:32400/library/metadata/1")
        return self.items.get(key)

    def selected(self, key: str, target: str) -> str | None:
        return self.selected_keys.get((key, target))

    def upload(self, key: str, target: str, jpeg: bytes) -> str:
        assert jpeg[:2] == b"\xff\xd8"
        self.uploads.append((key, target))
        image_key = f"upload-{len(self.uploads)}"
        self.selected_keys[(key, target)] = image_key
        return image_key

    def lock(self, item: Any, target: str) -> None:
        pass

    def restore(self, item: Any, target: str) -> None:
        self.restored.append((str(item["ratingKey"]), target))

    def remove_label(self, item: Any, label: str) -> None:
        self.items["1"]["Label"] = [t for t in self.items["1"].get("Label", []) if t["tag"] != label]

    def poster_bytes(self, item: Any) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (600, 900), (10, 120, 200)).save(buffer, "JPEG")
        return buffer.getvalue()


class Alerts(Notifier):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def alert(self, subject: str, message: str) -> None:
        self.sent.append(subject)


def make(tmp_path: Path, **env: str) -> tuple[Worker, FakePlex, Store, Alerts]:
    cfg = config.load(
        {"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "PLEX_URL": "http://plex.example:32400", **env}
    )
    plex, store, alerts = FakePlex(), Store(tmp_path / "state.db"), Alerts()
    worker = Worker(cfg, plex, store, alerts, FakeTmdb([ref("/english.jpg", "en")]))  # type: ignore[arg-type]
    worker.ctx.read, worker.ctx.fetch = read, fetch
    return worker, plex, store, alerts


def test_dry_run_writes_previews_once(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path)
    assert worker.process("1") == Outcome.PREVIEW
    assert (tmp_path / "previews" / "1-poster.jpg").exists()
    assert (tmp_path / "previews" / "1-art.jpg").exists()
    assert worker.process("1") == Outcome.UNCHANGED
    assert plex.uploads == []


def test_rest_frees_images_and_keeps_results(tmp_path: Path) -> None:
    worker, _, _, _ = make(tmp_path)
    worker.ctx.fetch = lru_cache(maxsize=8)(fetch)
    assert worker.process("1") == Outcome.PREVIEW
    worker.rest()
    assert worker.ctx.fetch.cache_info().currsize == 0
    assert worker.process("1") == Outcome.UNCHANGED


def test_upload_then_leave_a_manual_change_alone(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    assert sorted(plex.uploads) == [("1", "art"), ("1", "poster")]
    assert worker.process("1") == Outcome.UNCHANGED
    plex.selected_keys[("1", "poster")] = "chosen-by-hand"
    assert worker.process("1") == Outcome.MANUAL
    record = store.get("1", "poster")
    assert record is not None and record.status == Status.MANUAL
    assert len(plex.uploads) == 2


def test_a_changed_input_renders_again(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.items["1"]["Media"] = [{"videoResolution": "4k", "Part": []}]
    assert worker.process("1") == Outcome.UPLOADED
    assert plex.uploads.count(("1", "poster")) == 2


def test_failures_alert_once_per_upstream_after_three(tmp_path: Path) -> None:
    worker, plex, store, alerts = make(tmp_path)
    plex.fail = True
    for _ in range(3):
        assert worker.process("1") == Outcome.FAILED
    assert alerts.sent == ["Plex"]
    plex.fail = False
    assert worker.process("1") == Outcome.PREVIEW
    assert store.get("1", "item") is None


@pytest.mark.parametrize(("only", "expected"), [("", Outcome.PREVIEW), ("1", Outcome.PREVIEW), ("2", Outcome.SKIPPED)])
def test_only_rating_keys_limits_the_rollout(tmp_path: Path, only: str, expected: Outcome) -> None:
    worker, _, _, _ = make(tmp_path, ONLY_RATING_KEYS=only)
    assert worker.process("1") == expected


def test_a_removed_item_is_forgotten(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path)
    worker.process("1")
    del plex.items["1"]
    assert worker.process("1") == Outcome.GONE
    assert store.keys() == set()


def test_next_label_switches_art_once_and_is_removed(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.items["1"]["Label"] = [{"tag": "posteryard-next"}]
    assert worker.process("1") == Outcome.UPLOADED
    override = store.override("1")
    assert override is not None and override.skip
    assert plex.items["1"]["Label"] == []
    assert worker.process("1") == Outcome.UNCHANGED


def test_custom_label_adopts_the_poster_uploaded_in_plex(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.selected_keys[("1", "poster")] = "uploaded-by-hand"
    plex.items["1"]["Label"] = [{"tag": "Posteryard-Custom"}]
    assert worker.process("1") == Outcome.UPLOADED
    override = store.override("1")
    assert override is not None and override.source == "plex" and override.custom
    assert worker.process("1") == Outcome.UNCHANGED
    plex.items["1"]["Label"] = []
    assert worker.process("1") == Outcome.UPLOADED
    assert store.override("1") is None


def test_art_commands(tmp_path: Path) -> None:
    worker, _, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    assert worker.set_custom("1", Image.new("RGB", (800, 1200), (1, 2, 3))) == Outcome.UPLOADED
    assert worker.next_art("1") == Outcome.UPLOADED
    assert store.override("1") is None
    assert worker.next_art("1") == Outcome.UPLOADED
    assert worker.reset_art("1") == Outcome.UPLOADED
    assert store.override("1") is None


def test_the_ignore_label_leaves_the_item_alone(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    plex.items["1"]["Label"] = [{"tag": "posteryard-ignore"}, {"tag": "posteryard-next"}]
    assert worker.process("1") == Outcome.SKIPPED
    assert plex.uploads == []
    assert store.override("1") is None


def test_episodes_off_gives_back_plex_thumbnails(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false", EPISODE_THUMBNAILS="off")
    episode = {"ratingKey": "5", "type": "episode", "title": "Pilot", "grandparentRatingKey": "404",
               "librarySectionTitle": "TV Shows"}  # fmt: skip
    plex.items["5"], plex.items["6"] = episode, {**episode, "ratingKey": "6"}
    store.uploaded("5", "thumb", "Pilot", "abc", "upload-1")
    plex.selected_keys[("5", "thumb")] = "upload-1"
    worker.process("5")
    assert plex.restored == [("5", "thumb")]
    assert store.get("5", "thumb") is None
    store.uploaded("6", "thumb", "Pilot", "abc", "upload-2")
    plex.selected_keys[("6", "thumb")] = "chosen-by-hand"
    worker.process("6")
    assert plex.restored == [("5", "thumb")]
    assert store.get("6", "thumb") is None


def test_items_outside_the_libraries_are_skipped(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path)
    plex.items["7"] = {**MOVIE, "ratingKey": "7", "librarySectionTitle": "Home Videos"}
    plex.items["8"] = {k: v for k, v in MOVIE.items() if k != "librarySectionTitle"} | {"ratingKey": "8"}
    assert worker.process("7") == Outcome.SKIPPED
    assert worker.process("8") == Outcome.SKIPPED


def test_restore_all_gives_back_uploads_and_keeps_hand_changes(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="true")
    plex.items["2"] = {**MOVIE, "ratingKey": "2"}
    store.uploaded("1", "poster", "One", "fp", "upload-1")
    store.uploaded("1", "art", "One", "fp", "upload-2")
    store.uploaded("2", "poster", "Two", "fp", "upload-3")
    store.uploaded("3", "poster", "Gone", "fp", "upload-4")
    store.failed("1", "item", "One", "boom")
    plex.selected_keys = {("1", "poster"): "upload-1", ("1", "art"): "upload-2", ("2", "poster"): "by-hand"}
    counts = worker.restore_all()
    assert (counts.restored, counts.kept, counts.failed) == (2, 1, 0)
    assert plex.restored == [("1", "art"), ("1", "poster")]
    assert store.with_upload() == []
    assert store.get("1", "item") is not None


def test_restore_all_keeps_records_it_could_not_restore(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path)
    store.uploaded("1", "poster", "One", "fp", "upload-1")
    plex.fail = True
    assert worker.restore_all().failed == 1
    assert store.get("1", "poster") is not None


def test_restore_all_covers_uploads_rewritten_by_a_dry_run_and_half_finished_restores(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path)
    store.uploaded("1", "poster", "One", "fp", "upload-1")
    store.previewed("1", "poster", "One", "fp2")
    store.uploaded("1", "art", "One", "fp", "upload-2")
    plex.selected_keys = {("1", "poster"): "upload-1"}
    counts = worker.restore_all()
    assert (counts.restored, counts.kept) == (2, 0)
    assert sorted(plex.restored) == [("1", "art"), ("1", "poster")]


def test_an_item_is_planned_again_without_apple_art_that_failed_to_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, _, _, _ = make(tmp_path)
    calls: list[int] = []

    def plan_item(ctx: pipeline.Context, item: Item) -> list[pipeline.Plan]:
        calls.append(1)
        if len(calls) == 1:
            ctx.apple_dropped = True
            raise http.HttpError(503, "https://is1-ssl.mzstatic.com/image/thumb/art.jpg")
        return []

    monkeypatch.setattr(pipeline, "plan_item", plan_item)
    assert worker.process("1") is not Outcome.FAILED
    assert len(calls) == 2
