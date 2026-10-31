import logging
import sqlite3
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from PIL import Image

from posteryard import cli, http, pipeline, why
from posteryard.jellyfin import Emby
from posteryard.maintainerr import Maintainerr
from posteryard.notify import Notifier
from posteryard.plex import Plex
from posteryard.worker import Outcome, Restored
from tests.test_lookup import FakePlex

ENV = {"TMDB_API_KEY": "example", "PLEX_URL": "http://plex.example:32400", "PLEX_TOKEN": "example"}


class FakeWorker:
    calls: ClassVar[list[tuple[str, str]]] = []

    restored: ClassVar[Restored] = Restored()

    def __init__(self, *args: Any) -> None:
        FakeWorker.calls = []
        self.server = args[1]
        self.ctx = SimpleNamespace(sources=SimpleNamespace())

    def process(self, key: str) -> Outcome:
        self.calls.append(("process", key))
        return Outcome.UPLOADED

    def next_art(self, key: str) -> Outcome:
        self.calls.append(("next", key))
        return Outcome.PREVIEW

    def reset_art(self, key: str) -> Outcome:
        self.calls.append(("reset", key))
        return Outcome.UPLOADED

    def set_custom(self, key: str, image: Any) -> Outcome:
        self.calls.append(("set", key))
        return Outcome.UPLOADED

    def restore_all(self) -> Restored:
        return self.restored


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    for name, value in {**ENV, "DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "Plex", lambda url, token: FakePlex())
    monkeypatch.setattr(cli, "Worker", FakeWorker)
    return cli.main


def test_art_next_by_name_without_quotes(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["art", "next", "the", "office"]) == 0
    assert FakeWorker.calls == [("next", "5646")]
    assert "The Office (2005, show): preview (DRY_RUN is on" in capsys.readouterr().out


def test_art_reset_a_season(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["art", "reset", "The Office", "--season", "2"]) == 0
    assert FakeWorker.calls == [("reset", "5648")]
    assert capsys.readouterr().out.strip() == "The Office (2005) Season 2: uploaded"


def test_an_ambiguous_name_changes_nothing(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["art", "set", "Dune", "--url", "https://example.org/a.jpg"]) == 1
    assert FakeWorker.calls == []
    out = capsys.readouterr().out
    assert 'posteryard art set "Dune 2021" --url https://example.org/a.jpg   or 8120' in out


def test_a_missing_season_changes_nothing(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["forget", "The Office", "--season", "7"]) == 1
    assert FakeWorker.calls == []
    assert "has no season 7" in capsys.readouterr().out


def test_forget_hands_the_title_back_and_renders_it(run: Any) -> None:
    assert run(["forget", "5646"]) == 0
    assert FakeWorker.calls == [("process", "5646")]


def test_find_lists_keys(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["find", "dune"]) == 0
    assert capsys.readouterr().out.splitlines() == ["  4411  Dune (1984, movie)", "  8120  Dune (2021, movie)"]
    assert run(["find", "zzyzx"]) == 1


def test_commands_that_change_plex_need_plex_settings(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PLEX_TOKEN")
    assert run(["art", "next", "The Office"]) == 2
    assert "PLEX_URL and PLEX_TOKEN" in capsys.readouterr().out


def test_preview_needs_something_to_render(run: Any) -> None:
    assert run(["preview"]) == 2
    assert run(["preview", "--tmdb", "film:1"]) == 2


def test_test_alert_needs_a_service(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["test-alert"]) == 1
    assert "NOTIFY_URLS" in capsys.readouterr().out


def test_health_asks_the_service_on_its_port(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status = [200]

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status[0] if self.path == "/healthz" else 404)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LISTEN_PORT", str(server.server_address[1]))
        assert run(["health"]) == 0
        status[0] = 503
        assert run(["health"]) == 1
        assert "HTTP 503" in capsys.readouterr().out
    finally:
        server.shutdown()
        server.server_close()
    assert run(["health"]) == 1
    assert "not reachable" in capsys.readouterr().out


def test_a_database_from_a_newer_release_stops_the_command(
    run: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with closing(sqlite3.connect(tmp_path / "state.db")) as db:
        db.execute("PRAGMA user_version=999")
    assert run(["forget", "5646"]) == 2
    assert "newer than this Posteryard supports" in capsys.readouterr().out


def test_restore_needs_all(run: Any) -> None:
    with pytest.raises(SystemExit):
        run(["restore"])


def test_debug_logging_keeps_url_logging_libraries_quiet(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    run(["find", "dune"])
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("urllib3").level == logging.WARNING
    logging.getLogger().setLevel(logging.WARNING)


def test_a_data_folder_from_another_server_stops_the_command(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["forget", "5646"]) == 0
    other = FakePlex()
    other.identity = "emby:other"
    monkeypatch.setattr(cli, "Plex", lambda url, token: other)
    FakeWorker.calls = []
    assert run(["forget", "5646"]) == 2
    assert "belongs to another media server (plex:example)" in capsys.readouterr().out
    assert FakeWorker.calls == []


def plan(key: str) -> pipeline.Plan:
    return pipeline.Plan(key, "poster", "Example", {}, lambda: Image.new("RGB", (4, 6)), ["art from TMDB"])


def test_preview_from_tmdb_saves_each_plan_and_counts_failures(
    run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def plan_tmdb(ctx: Any, kind: str, tmdb_id: int, seasons: list[int]) -> list[pipeline.Plan]:
        if tmdb_id == 404:
            raise pipeline.NotFoundError("TMDB has no movie 404")
        return [plan(f"{kind}-{tmdb_id}")]

    monkeypatch.setattr(pipeline, "plan_tmdb", plan_tmdb)
    out = tmp_path / "out"
    assert run(["preview", "--tmdb", "movie:603", "--out", str(out)]) == 0
    assert [p.name for p in out.iterdir()] == ["movie-603-poster-example.jpg"]
    assert run(["preview", "--tmdb", "movie:603", "--tmdb", "movie:404", "--out", str(out)]) == 1
    assert "movie:404: TMDB has no movie 404" in capsys.readouterr().out


def test_preview_of_a_title_renders_without_maintainerr(
    run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    keys: list[str] = []

    def plan_preview(ctx: Any, key: str, episodes: int | None) -> list[pipeline.Plan]:
        keys.append(key)
        return [plan(key)]

    def unavailable(self: Any) -> Any:
        raise http.RequestError("ConnectionError", "http://maintainerr.example:6246/api/collections")

    monkeypatch.setattr(pipeline, "plan_preview", plan_preview)
    monkeypatch.setattr(Maintainerr, "action_days", unavailable)
    assert run(["preview", "The", "Office", "--season", "2", "--out", str(tmp_path / "out")]) == 0
    assert keys == ["5648"]
    assert "Maintainerr unavailable" in capsys.readouterr().out


def test_preview_of_an_unknown_title_renders_nothing(run: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["preview", "zzyzx"]) == 1
    assert "zzyzx" in capsys.readouterr().out


def test_preview_reports_an_image_that_cannot_be_read(
    run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def gone(*args: Any) -> list[pipeline.Plan]:
        raise FileNotFoundError("No such file or directory: '/data/custom/5646-0123456789ab.jpg'")

    monkeypatch.setattr(pipeline, "plan_preview", gone)
    assert run(["preview", "5646", "--out", str(tmp_path / "out")]) == 1
    assert "5646: No such file" in capsys.readouterr().out


def test_live_tv_preview_needs_plex_or_jellyfin(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "Plex", Plex)
    for name in ("PLEX_URL", "PLEX_TOKEN"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("EMBY_URL", "http://emby.example:8096")
    monkeypatch.setenv("EMBY_API_KEY", "example")
    assert run(["preview", "--live-tv"]) == 2
    assert "not Emby" in capsys.readouterr().out
    monkeypatch.delenv("EMBY_URL")
    assert run(["preview", "--live-tv"]) == 2


def test_live_tv_is_left_out_on_emby(caplog: pytest.LogCaptureFixture) -> None:
    cfg = SimpleNamespace(live_tv=True)
    server = Emby("http://emby.example:8096", "example")
    assert cli._live_tv(cfg, server, None, None) is None  # type: ignore[arg-type]
    assert "Plex and Jellyfin only" in caplog.text
    assert cli._live_tv(SimpleNamespace(live_tv=False), server, None, None) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "case",
    [
        (Restored(restored=3), 0, "Gave 3 images back to Plex."),
        (Restored(restored=2, kept=1), 0, "Left 1 images that were changed by hand."),
        (Restored(restored=1, failed=2), 1, "2 images could not be restored."),
    ],
)
def test_restore_reports_what_it_did(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: tuple[Restored, int, str]
) -> None:
    counts, code, text = case
    monkeypatch.setattr(FakeWorker, "restored", counts)
    assert run(["restore", "--all"]) == code
    assert text in capsys.readouterr().out


def test_a_refused_test_alert_fails(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NOTIFY_URLS", "json://alerts.example/hook")
    monkeypatch.setattr(Notifier, "send", lambda self, *args, **kwargs: False)
    assert run(["test-alert"]) == 1
    assert "did not accept" in capsys.readouterr().out
    monkeypatch.setattr(Notifier, "send", lambda self, *args, **kwargs: True)
    assert run(["test-alert"]) == 0


def test_art_set_from_a_missing_file_changes_nothing(
    run: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["art", "set", "5646", "--file", str(tmp_path / "missing.jpg")]) == 1
    assert FakeWorker.calls == []
    assert "no such file" in capsys.readouterr().out


def test_why_explains_or_reports_what_it_could_not_read(
    run: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(why, "report", lambda ctx, item, store: "result")
    monkeypatch.setattr(why, "lines", lambda label, result: [f"{label}: {result}"])
    assert run(["why", "Dune", "2021"]) == 0
    assert capsys.readouterr().out.strip() == "Dune (2021, movie): result"

    def gone(*args: Any) -> None:
        raise FileNotFoundError("No such file or directory: '/data/custom/8120-0123456789ab.jpg'")

    monkeypatch.setattr(why, "report", gone)
    assert run(["why", "8120"]) == 1
    assert "Dune (2021, movie): No such file" in capsys.readouterr().out
    assert run(["why", "Dune"]) == 1
    assert run(["why", "zzyzx"]) == 1
