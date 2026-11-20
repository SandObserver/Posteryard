import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from posteryard import http

ROOT = Path(__file__).parent.parent
ANSWERS: dict[str, Any] = json.loads((ROOT / "tests" / "recorded_answers.json").read_text(encoding="utf-8"))


def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_answers", ROOT / "tools" / "record_answers.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = tool()


def test_recorded_answers_from_tmdb_fanart_wikidata_and_apple_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def replay(method: str, url: str, **kwargs: Any) -> bytes:
        key = TOOL.key_of(url)
        asked.append(key)
        assert key in ANSWERS, f"no recorded answer for {key}. Run: uv run python tools/record_answers.py"
        body: bytes = TOOL.body_of(ANSWERS[key])
        return body

    monkeypatch.setattr(http, "request", replay)
    summary = TOOL.exercise("example-key", "example-key")
    assert TOOL.problems(summary) == []
    assert sorted(set(asked)) == sorted(ANSWERS)


def test_recorded_answers_hold_no_credentials() -> None:
    text = json.dumps(ANSWERS)
    assert "api_key=%2A%2A%2A" in text
    assert all("api_key=" not in key or "api_key=%2A%2A%2A" in key for key in ANSWERS)
