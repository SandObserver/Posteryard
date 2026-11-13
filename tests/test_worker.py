import io
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from posteryard import config, http, pipeline, similar
from posteryard.notify import Notifier
from posteryard.server import Item, Target, made_here
from posteryard.store import Status, Store
from posteryard.worker import Outcome, Worker, jpeg
from tests.fakes import FakeServer, FakeTmdb, fetch, read, ref

MOVIE: Item = {
    "ratingKey": "1", "type": "movie", "title": "Example Movie", "librarySectionTitle": "Movies",
    "librarySectionID": "3", "Guid": [{"id": "tmdb://42"}], "Media": [],
}  # fmt: skip


class FakePlex(FakeServer):
    def __init__(self) -> None:
        self.items: dict[str, Item] = {"1": Item(**MOVIE)}
        self.selected_keys: dict[tuple[str, str], str] = {}
        self.uploads: list[tuple[str, str]] = []
        self.fail = False
        self.restored: list[tuple[str, str]] = []
        self.originals: list[str | None] = []

    def item(self, rating_key: str) -> Item | None:
        if self.fail:
            raise http.RequestError("ConnectionError", "http://plex.example:32400/library/metadata/1")
        return self.items.get(rating_key)

    def selected(self, rating_key: str, target: Target) -> str | None:
        return self.selected_keys.get((rating_key, target))

    def upload(self, rating_key: str, target: Target, jpeg: bytes) -> str:
        assert jpeg[:2] == b"\xff\xd8"
        self.uploads.append((rating_key, target))
        image_key = f"upload-{len(self.uploads)}"
        self.selected_keys[(rating_key, target)] = image_key
        return image_key

    def restore(self, item: Item, target: Target, original: str | None = None) -> None:
        self.restored.append((str(item["ratingKey"]), target))
        self.originals.append(original)

    def remove_label(self, item: Item, label: str) -> None:
        key = str(item["ratingKey"])
        self.items[key]["Label"] = [t for t in self.items[key].get("Label", []) if t["tag"] != label]

    def poster_bytes(self, item: Item) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (600, 900), (10, 120, 200)).save(buffer, "JPEG")
        return buffer.getvalue()


class Alerts(Notifier):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    def alert(self, subject: str, message: str) -> None:
        self.sent.append(subject)


def _env(tmp_path: Path) -> dict[str, str]:
    return {"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "PLEX_URL": "http://plex.example:32400"}


def make(tmp_path: Path, **env: str) -> tuple[Worker, FakePlex, Store, Alerts]:
    cfg = config.load(
        {"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "PLEX_URL": "http://plex.example:32400", **env}
    )
    plex, store, alerts = FakePlex(), Store(tmp_path / "state.db"), Alerts()
    worker = Worker(cfg, plex, store, alerts, FakeTmdb([ref("/english.jpg", "en")]))
    worker.ctx.sources.read, worker.ctx.sources.fetch = read, fetch
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
    worker.ctx.sources.fetch = lru_cache(maxsize=8)(fetch)
    assert worker.process("1") == Outcome.PREVIEW
    worker.ctx.measures.thumbs["/a.jpg"] = similar.thumb(Image.new("RGB", (60, 90)))
    worker.rest()
    assert worker.ctx.sources.fetch.cache_info().currsize == 0
    assert not worker.ctx.measures.thumbs
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


def test_a_dry_run_and_going_live_again_leave_a_hand_change_alone(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    plex.selected_keys[("1", "poster")] = "chosen-by-hand"
    worker.cfg = config.load({**_env(tmp_path), "DRY_RUN": "true"})
    assert worker.process("1") == Outcome.MANUAL
    worker.cfg = config.load({**_env(tmp_path), "DRY_RUN": "false"})
    assert worker.process("1") == Outcome.MANUAL
    assert plex.selected_keys[("1", "poster")] == "chosen-by-hand"
    assert len(plex.uploads) == 2


def test_a_dry_run_preview_of_an_uploaded_title_keeps_its_record(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    worker.cfg = config.load({**_env(tmp_path), "DRY_RUN": "true"})
    plex.items["1"]["Media"] = [{"videoResolution": "4k", "Part": []}]
    uploaded = plex.selected_keys[("1", "poster")]
    assert worker.process("1") == Outcome.PREVIEW
    record = store.get("1", "poster")
    assert record is not None and (record.status, record.image_key) == (Status.UPLOADED, uploaded)
    worker.cfg = config.load({**_env(tmp_path), "DRY_RUN": "false"})
    assert worker.process("1") == Outcome.UPLOADED
    assert plex.uploads.count(("1", "poster")) == 2
    assert plex.uploads.count(("1", "art")) == 1


def test_an_upload_stopped_halfway_is_redone_and_restored(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.items["1"]["Media"] = [{"videoResolution": "4k", "Part": []}]
    store.uploading("1", "poster", "Example Movie")
    plex.selected_keys[("1", "poster")] = "upload-halfway"
    assert worker.process("1") == Outcome.UPLOADED
    assert plex.uploads.count(("1", "poster")) == 2
    store.uploading("1", "poster", "Example Movie")
    store.uploading("2", "poster", "Two")
    plex.items["2"] = Item(**{**MOVIE, "ratingKey": "2"})
    plex.selected_keys[("1", "poster")] = "upload-halfway"
    assert worker.restore_all().restored == 3
    assert ("2", "poster") in plex.restored


def test_a_changed_input_renders_again(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.items["1"]["Media"] = [{"videoResolution": "4k", "Part": []}]
    assert worker.process("1") == Outcome.UPLOADED
    assert plex.uploads.count(("1", "poster")) == 2


def test_only_a_first_upload_counts_as_a_new_poster(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    assert [name for name, _ in worker.fresh] == [plex.items["1"]["title"]]
    assert worker.fresh[0][1].startswith(b"\xff\xd8")
    plex.items["1"]["Media"] = [{"videoResolution": "4k", "Part": []}]
    assert worker.process("1") == Outcome.UPLOADED
    assert worker.fresh == []


def test_a_title_previewed_before_is_not_new(tmp_path: Path) -> None:
    worker, _, _, _ = make(tmp_path)
    worker.process("1")
    worker.cfg = config.load(
        {
            "TMDB_API_KEY": "example",
            "DATA_DIR": str(tmp_path),
            "PLEX_URL": "http://plex.example:32400",
            "DRY_RUN": "false",
        }
    )
    assert worker.process("1") == Outcome.UPLOADED
    assert worker.fresh == []


def test_failures_alert_once_per_upstream_after_three(tmp_path: Path) -> None:
    worker, plex, store, alerts = make(tmp_path)
    plex.fail = True
    for _ in range(3):
        assert worker.process("1") == Outcome.FAILED
    assert alerts.sent == ["Plex"]
    plex.fail = False
    assert worker.process("1") == Outcome.PREVIEW
    assert store.get("1", "item") is None


def test_fanart_failures_alert_as_fanart_and_resolve_after_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, _, _, alerts = make(tmp_path)
    resolved: list[str] = []
    monkeypatch.setattr(alerts, "resolve", lambda cause, message: resolved.append(cause))
    down = [True]

    def plan_item(ctx: pipeline.Context, item: Item) -> list[pipeline.Plan]:
        if down[0]:
            raise http.TemporaryError("ConnectTimeoutError", "https://webservice.fanart.tv/v3/movies/1")
        return []

    monkeypatch.setattr(pipeline, "plan_item", plan_item)
    for _ in range(3):
        assert worker.process("1") == Outcome.FAILED
    assert alerts.sent == ["fanart.tv"]
    down[0] = False
    worker.process("1")
    assert "fanart.tv" in resolved


def test_an_unmatched_title_is_skipped_without_retries_or_alerts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worker, plex, store, alerts = make(tmp_path)
    store.failed("1", "item", "Example Movie", "earlier failure")
    plex.items["1"]["Guid"] = []
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            assert worker.process("1") == Outcome.SKIPPED
    assert alerts.sent == []
    assert store.get("1", "item") is None
    assert store.retry_due(now=float("inf")) == []
    assert caplog.records[-1].getMessage() == "not matched to TMDB, match it in Plex to get a poster"


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


def test_next_label_keeps_its_text_and_skips_once_when_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.items["1"]["Label"] = [{"tag": "Posteryard-Next"}]
    removed: list[str] = []

    def failing(item: Item, label: str) -> None:
        removed.append(label)
        raise http.RequestError("HTTP 500", "http://plex.example:32400/library/sections/3/all")

    monkeypatch.setattr(plex, "remove_label", failing)
    assert worker.process("1") == Outcome.FAILED
    assert worker.process("1") == Outcome.FAILED
    assert removed == ["Posteryard-Next", "Posteryard-Next"]
    assert store.override("1") is None
    monkeypatch.undo()
    assert worker.process("1") == Outcome.UPLOADED
    override = store.override("1")
    assert override is not None and len(override.skip) == 1
    assert plex.items["1"]["Label"] == []


def test_next_label_on_an_episode_is_removed_and_the_thumbnail_still_updates(tmp_path: Path) -> None:
    worker, plex, store, alerts = make(tmp_path, DRY_RUN="false")
    plex.items["3"] = {"ratingKey": "3", "type": "show", "title": "Example Show", "Guid": [{"id": "tmdb://42"}],
                       "librarySectionTitle": "TV Shows"}  # fmt: skip
    plex.items["5"] = {"ratingKey": "5", "type": "episode", "title": "Pilot", "index": 1, "parentIndex": 1,
                       "grandparentRatingKey": "3", "librarySectionTitle": "TV Shows",
                       "Label": [{"tag": "posteryard-next"}]}  # fmt: skip
    assert worker.process("5") == Outcome.UPLOADED
    assert plex.items["5"]["Label"] == []
    assert store.override("5") is None
    assert alerts.sent == []


def test_art_commands_leave_a_background_changed_by_hand(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.selected_keys[("1", "art")] = "chosen-by-hand"
    for command in (worker.next_art, worker.reset_art):
        plex.uploads.clear()
        assert command("1") == Outcome.UPLOADED
        assert plex.uploads == [("1", "poster")]
    assert plex.selected_keys[("1", "art")] == "chosen-by-hand"
    record = store.get("1", "art")
    assert record is not None and record.status == Status.MANUAL


def test_next_label_changes_nothing_in_a_dry_run(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path)
    plex.items["1"]["Label"] = [{"tag": "posteryard-next"}]
    assert worker.process("1") == Outcome.PREVIEW
    assert plex.items["1"]["Label"] == [{"tag": "posteryard-next"}]
    assert store.override("1") is None


def test_custom_label_adopts_the_poster_uploaded_in_plex(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.selected_keys[("1", "poster")] = "uploaded-by-hand"
    plex.items["1"]["Label"] = [{"tag": "Posteryard-Custom"}]
    assert worker.process("1") == Outcome.UPLOADED
    override = store.override("1")
    assert override is not None and override.source == "label" and override.custom
    assert worker.process("1") == Outcome.UNCHANGED
    plex.items["1"]["Label"] = []
    assert worker.process("1") == Outcome.UPLOADED
    assert store.override("1") is None


def test_custom_art_from_an_older_release_ends_when_the_label_goes(tmp_path: Path) -> None:
    worker, _, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    store.set_custom("1", str(tmp_path / "custom.jpg"), "plex")
    worker.process("1")
    assert store.override("1") is None


def custom_poster(tmp_path: Path) -> tuple[Worker, FakePlex, Store, str]:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    images: dict[str, bytes] = {}
    upload = plex.upload

    def keep(rating_key: str, target: Target, data: bytes) -> str:
        images[key := upload(rating_key, target, data)] = data
        return key

    def selected_bytes(item: Item) -> bytes:
        return images.get(plex.selected_keys[(str(item["ratingKey"]), "poster")]) or FakePlex.poster_bytes(plex, item)

    plex.upload = keep  # type: ignore[method-assign,assignment]
    plex.poster_bytes = selected_bytes  # type: ignore[method-assign]
    worker.process("1")
    plex.selected_keys[("1", "poster")] = "uploaded-by-hand"
    plex.items["1"]["Label"] = [{"tag": "posteryard-custom"}]
    assert worker.process("1") == Outcome.UPLOADED
    override = store.override("1")
    assert override is not None and override.custom
    return worker, plex, store, override.custom


def test_custom_art_is_kept_after_forget(tmp_path: Path) -> None:
    worker, _, store, custom = custom_poster(tmp_path)
    store.forget("1")
    assert worker.process("1") == Outcome.UPLOADED
    override = store.override("1")
    assert override is not None and override.custom == custom
    assert Path(custom).exists()


def test_custom_art_ignores_a_poster_posteryard_made_with_another_database(tmp_path: Path) -> None:
    worker, _, store, custom = custom_poster(tmp_path)
    store.forget("1")
    store.drop_uploads("1")
    worker.process("1")
    override = store.override("1")
    assert override is not None and override.custom == custom


def test_posteryard_jpegs_carry_a_mark() -> None:
    image = Image.new("RGB", (60, 90), (10, 20, 30))
    assert made_here(jpeg(image))
    plain = io.BytesIO()
    image.save(plain, "JPEG", comment=b"Other tool")
    assert not made_here(plain.getvalue())
    assert not made_here(b"not a jpeg")
    assert not made_here(jpeg(image)[:4])


def test_removing_the_next_label_counts_a_later_one_as_new(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.items["1"]["Label"] = [{"tag": "posteryard-next"}]
    store.relabel("posteryard-next", {"1"})
    worker.process("1")
    assert store.relabel("posteryard-next", {"1"}) == ({"1"}, set())


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
    episode: Item = {"ratingKey": "5", "type": "episode", "title": "Pilot", "grandparentRatingKey": "404",
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
    record = store.get("6", "thumb")
    assert record is not None and record.status == Status.MANUAL


def test_episodes_off_gives_back_a_thumbnail_whose_upload_stopped_halfway(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false", EPISODE_THUMBNAILS="off")
    plex.items["5"] = {"ratingKey": "5", "type": "episode", "title": "Pilot", "grandparentRatingKey": "404",
                       "librarySectionTitle": "TV Shows"}  # fmt: skip
    store.uploading("5", "thumb", "Pilot")
    worker.process("5")
    assert plex.restored == [("5", "thumb")]
    assert store.get("5", "thumb") is None


def test_a_dry_run_keeps_the_record_of_a_thumbnail_it_does_not_give_back(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, EPISODE_THUMBNAILS="off")
    plex.items["5"] = {"ratingKey": "5", "type": "episode", "title": "Pilot", "grandparentRatingKey": "404",
                       "librarySectionTitle": "TV Shows"}  # fmt: skip
    store.uploaded("5", "thumb", "Pilot", "abc", "upload-1")
    plex.selected_keys[("5", "thumb")] = "upload-1"
    worker.process("5")
    assert plex.restored == []
    assert store.get("5", "thumb") is not None


def test_an_unexpected_error_counts_as_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker, plex, store, alerts = make(tmp_path)

    def broken(rating_key: str) -> Item | None:
        raise KeyError("Metadata")

    monkeypatch.setattr(plex, "item", broken)
    for _ in range(3):
        assert worker.process("1") == Outcome.FAILED
    assert alerts.sent == ["rendering"]
    record = store.get("1", "item")
    assert record is not None and record.failures == 3
    assert store.retry_due(now=record.updated_at + 10**6) == ["1"]


def test_items_outside_the_libraries_are_skipped(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path)
    plex.items["7"] = {**MOVIE, "ratingKey": "7", "librarySectionTitle": "Home Videos"}
    unlisted: Item = {**MOVIE, "ratingKey": "8"}
    del unlisted["librarySectionTitle"]
    plex.items["8"] = unlisted
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
    assert [(r.rating_key, r.status) for r in store.with_upload()] == [("2", Status.MANUAL)]
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
            ctx.sources.apple_dropped = True
            raise http.HttpError(503, "https://is1-ssl.mzstatic.com/image/thumb/art.jpg")
        return []

    monkeypatch.setattr(pipeline, "plan_item", plan_item)
    assert worker.process("1") is not Outcome.FAILED
    assert len(calls) == 2


def test_apple_art_dropped_before_the_item_does_not_plan_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, _, _, _ = make(tmp_path)
    calls: list[int] = []

    def plan_item(ctx: pipeline.Context, item: Item) -> list[pipeline.Plan]:
        calls.append(1)
        raise http.HttpError(500, "https://plex.example/library/metadata/1")

    monkeypatch.setattr(pipeline, "plan_item", plan_item)
    worker.ctx.sources.apple_dropped = True
    assert worker.process("1") is Outcome.FAILED
    assert len(calls) == 1


def test_an_unreadable_maintainerr_answer_keeps_the_saved_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, _, store, alerts = make(tmp_path)
    store.set_meta("leaving_days", '{"1": "2026-11-01"}')

    def nested() -> dict[str, date]:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(worker.maintainerr, "action_days", nested)
    assert worker.leaving_days() == {"1": date(2026, 11, 1)}
    assert alerts.sent == ["Maintainerr"]


def test_restore_all_gives_back_the_image_shown_before_the_first_upload(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    plex.selected_keys[("1", "poster")] = "upload://posters/own-upload"
    worker.process("1")
    worker.process("1", force=True)
    assert store.original("1", "poster") == "upload://posters/own-upload"
    worker.restore_all()
    assert dict(zip(plex.restored, plex.originals, strict=True))[("1", "poster")] == "upload://posters/own-upload"


def test_restore_all_finds_uploads_whose_record_was_forgotten(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    worker.cfg = config.load({**_env(tmp_path), "DRY_RUN": "true"})
    store.forget("1")
    assert worker.process("1") == Outcome.PREVIEW
    plex.selected_keys[("1", "art")] = "chosen-by-hand"
    counts = worker.restore_all()
    assert (counts.restored, counts.kept) == (1, 0)
    assert plex.restored == [("1", "poster")]


def test_a_dry_run_after_an_interrupted_first_upload_keeps_it_restorable(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    upload = plex.upload

    def stopped(rating_key: str, target: Target, data: bytes) -> str:
        upload(rating_key, target, data)
        raise http.RequestError("ConnectionError", "http://plex.example:32400/library/metadata/1/posters")

    plex.upload = stopped  # type: ignore[method-assign, assignment]
    assert worker.process("1") == Outcome.FAILED
    worker.cfg = config.load({**_env(tmp_path), "DRY_RUN": "true"})
    assert worker.process("1") == Outcome.PREVIEW
    record = store.get("1", "poster")
    assert record is not None and record.status == Status.UPLOADING
    worker.restore_all()
    assert ("1", "poster") in plex.restored


def test_a_failed_lock_is_retried(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    locks: list[str] = []

    def lock(item: Item, target: Target) -> None:
        locks.append(target)
        if len(locks) == 1:
            raise http.HttpError(500, "http://plex.example:32400/library/sections/3/all")

    plex.lock = lock  # type: ignore[method-assign]
    assert worker.process("1") == Outcome.FAILED
    record = store.get("1", plex.uploads[0][1])
    assert record is not None and record.image_key == plex.selected_keys[("1", plex.uploads[0][1])]
    assert "1" in store.retry_due(now=10**12)
    assert worker.process("1") == Outcome.UPLOADED
    assert len(locks) == 3
    assert worker.process("1") == Outcome.UNCHANGED


def test_hand_changes_stay_protected_after_restore_all(tmp_path: Path) -> None:
    worker, plex, _, _ = make(tmp_path, DRY_RUN="false")
    worker.process("1")
    plex.selected_keys[("1", "poster")] = "chosen-by-hand"
    assert worker.restore_all().kept == 1
    worker.process("1")
    assert plex.selected_keys[("1", "poster")] == "chosen-by-hand"
    assert plex.uploads.count(("1", "poster")) == 1


def test_episode_thumbnails_changed_by_hand_stay_after_turning_thumbnails_on(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false", EPISODE_THUMBNAILS="off")
    plex.items["6"] = {"ratingKey": "6", "type": "episode", "title": "Pilot", "grandparentRatingKey": "404",
                       "librarySectionTitle": "TV Shows"}  # fmt: skip
    store.uploaded("6", "thumb", "Pilot", "abc", "upload-2")
    plex.selected_keys[("6", "thumb")] = "chosen-by-hand"
    worker.process("6")
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    plex.items["6"] = {"ratingKey": "6", "type": "episode", "title": "Pilot", "grandparentRatingKey": "404",
                       "librarySectionTitle": "TV Shows"}  # fmt: skip
    plex.selected_keys[("6", "thumb")] = "chosen-by-hand"
    worker.process("6")
    assert plex.uploads == []
    record = store.get("6", "thumb")
    assert record is not None and record.status == Status.MANUAL


def test_a_404_from_something_other_than_the_server_erases_nothing(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    store.manual("1", "art", "Example Movie")
    del plex.items["1"]

    def down() -> str:
        raise http.HttpError(404, "http://plex.example:32400/identity")

    plex.server_id = down  # type: ignore[method-assign]
    assert worker.process("1") == Outcome.FAILED
    record = store.get("1", "art")
    assert record is not None and record.status == Status.MANUAL
    assert store.upload_targets()


def test_an_answer_from_another_server_erases_nothing(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    store.set_meta("server", "plex:another")
    del plex.items["1"]
    assert worker.process("1") == Outcome.FAILED
    assert store.get("1", "poster") is not None


def test_a_title_the_server_says_is_gone_is_forgotten(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    store.set_meta("server", "plex:example")
    del plex.items["1"]
    assert worker.process("1") == Outcome.GONE
    assert store.get("1", "poster") is None


def test_restore_goes_on_after_a_malformed_answer(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    plex.items["2"] = Item(**{**MOVIE, "ratingKey": "2"})
    store.uploaded("1", "poster", "A", "f", "upload-a")
    store.uploaded("2", "poster", "B", "f", "upload-b")
    plex.selected_keys[("2", "poster")] = "upload-b"
    real = plex.selected

    def broken(key: str, target: Target) -> str | None:
        if key == "1":
            raise AttributeError("'str' object has no attribute 'get'")
        return real(key, target)

    plex.selected = broken  # type: ignore[assignment]
    counts = worker.restore_all()
    assert (counts.failed, counts.restored) == (1, 1)
    assert store.get("1", "poster") is not None


def test_a_new_tag_on_our_own_image_is_not_a_change_by_hand(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    plex.selected_keys[("1", "poster")] = "new-tag"
    plex.still_ours = lambda item, target: True  # type: ignore[method-assign]
    assert worker.process("1") == Outcome.UNCHANGED
    record = store.get("1", "poster")
    assert record is not None and record.status == Status.UPLOADED and record.image_key == "new-tag"


def test_an_uploaded_poster_stays_while_apple_art_is_unavailable(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    store.uploaded("1", "poster", "Example Movie", "older", plex.selected_keys[("1", "poster")])
    item = plex.items["1"]
    plan = next(p for p in pipeline.plan_item(worker.ctx, item) if p.target == "poster")
    worker.ctx.sources.apple_unavailable = True
    count = len(plex.uploads)
    assert worker._apply(plan, item, force=False) == Outcome.UNCHANGED
    assert len(plex.uploads) == count


def _fail_once(plex: FakePlex, name: str) -> None:
    real, calls = getattr(plex, name), []

    def once(*args: Any) -> Any:
        calls.append(args)
        if len(calls) == 1:
            raise http.RequestError("server error", "http://plex.example")
        return real(*args)

    setattr(plex, name, once)


def test_a_refresh_after_a_failed_lock_is_not_a_change_by_hand(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    _fail_once(plex, "lock")
    worker.process("1")
    target = next(t for t in ("poster", "art") if (r := store.get("1", t)) and r.fingerprint == "")
    plex.selected_keys[("1", target)] = "plex-agent-image"
    worker.process("1")
    record = store.get("1", target)
    assert record is not None and record.status == Status.UPLOADED and record.fingerprint


def test_an_image_chosen_before_the_retry_is_kept(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    plex.selected_keys[("1", "poster")] = "plex-agent-image"
    _fail_once(plex, "upload")
    worker.process("1")
    plex.selected_keys[("1", "poster")] = "chosen-by-hand"
    worker.process("1")
    record = store.get("1", "poster")
    assert record is not None and record.status == Status.MANUAL


def test_a_refresh_after_a_failed_lock_keeps_the_original(tmp_path: Path) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    plex.selected_keys[("1", "poster")] = "users-own"
    plex.selected_keys[("1", "art")] = "users-own-art"
    _fail_once(plex, "lock")
    worker.process("1")
    target = next(t for t in ("poster", "art") if (r := store.get("1", t)) and r.fingerprint == "")
    original = store.original("1", target)
    plex.selected_keys[("1", target)] = "plex-agent-image"
    worker.process("1")
    assert original and store.original("1", target) == original


def test_a_failed_lock_is_locked_again_during_an_apple_outage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    _fail_once(plex, "lock")
    worker.process("1")
    pending = next(t for t in ("poster", "art") if (r := store.get("1", t)) and r.fingerprint == "")
    locks: list[Target] = []

    def lock(item: Item, target: Target) -> None:
        locks.append(target)

    plex.lock = lock  # type: ignore[method-assign]
    real = pipeline.plan_item

    def plan(ctx: pipeline.Context, item: Item) -> Any:
        plans = real(ctx, item)
        ctx.sources.apple_unavailable = True
        return plans

    monkeypatch.setattr(pipeline, "plan_item", plan)
    worker.process("1")
    assert pending in locks


def test_a_poster_chosen_after_a_lock_during_an_apple_outage_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    _fail_once(plex, "lock")
    worker.process("1")
    target = next(t for t in ("poster", "art") if (r := store.get("1", t)) and r.fingerprint == "")
    real = pipeline.plan_item

    def outage(ctx: pipeline.Context, item: Item) -> Any:
        plans = real(ctx, item)
        ctx.sources.apple_unavailable = True
        return plans

    monkeypatch.setattr(pipeline, "plan_item", outage)
    worker.process("1")
    monkeypatch.setattr(pipeline, "plan_item", real)
    plex.selected_keys[("1", target)] = "hand-picked"
    worker.process("1")
    assert plex.selected_keys[("1", target)] == "hand-picked"


def _fail_first_logo(worker: Worker) -> None:
    real = worker.ctx.sources.fetch
    state = {"fail": True}

    def flaky(path: str) -> Any:
        if path.endswith(".png") and state["fail"]:
            state["fail"] = False
            raise http.TemporaryError("ReadTimeoutError", "https://image.tmdb.org/t/p/original/logo.png")
        return real(path)

    worker.ctx.sources.fetch = flaky


@pytest.mark.parametrize("label", ["posteryard-custom", "posteryard-next"])
def test_a_label_on_a_poster_changed_by_hand_survives_one_failed_run(tmp_path: Path, label: str) -> None:
    worker, plex, store, _ = make(tmp_path, DRY_RUN="false")
    assert worker.process("1") == Outcome.UPLOADED
    plex.selected_keys[("1", "poster")] = "chosen-by-hand"
    assert worker.process("1") == Outcome.MANUAL
    plex.items["1"]["Label"] = [{"tag": label}]
    _fail_first_logo(worker)
    assert worker.process("1") == Outcome.FAILED
    assert worker.process("1") == Outcome.UPLOADED
    record = store.get("1", "poster")
    assert record is not None and record.status == Status.UPLOADED
