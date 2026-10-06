import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, ClassVar

import pytest

from posteryard import cli
from posteryard.worker import Outcome
from tests.test_lookup import FakePlex

ENV = {"TMDB_API_KEY": "example", "PLEX_URL": "http://plex.example:32400", "PLEX_TOKEN": "example"}


class FakeWorker:
    calls: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, *args: Any) -> None:
        FakeWorker.calls = []

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
