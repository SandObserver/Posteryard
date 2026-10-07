import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest

from posteryard import config, notify, quality
from posteryard.config import EpisodeMode, LogLevel
from posteryard.quality import AudioLevel, HdrLevel, VideoLevel

ROOT = Path(__file__).parent.parent
Setting = dict[str, Any]
PAGE = json.loads((ROOT / "site" / "settings.json").read_text())
SETTINGS = [s for group in PAGE["groups"] for s in group["settings"]]
BASE = {"TMDB_API_KEY": "example"}
CHOICES: dict[str, set[str]] = {
    "QUALITY_MIN_VIDEO": {level.value for level in VideoLevel},
    "QUALITY_MIN_HDR": {level.value for level in HdrLevel},
    "QUALITY_MIN_AUDIO": {level.value for level in AudioLevel},
    "QUALITY_ACCESSIBILITY": {badge.value for badge in quality.ACCESSIBILITY},
    "EPISODE_THUMBNAILS": {mode.value for mode in EpisodeMode},
    "NOTIFY_EVENTS": {event.value for event in notify.Event},
    "LOG_LEVEL": {level.value for level in LogLevel},
}


def _read_names() -> set[str]:
    source = (ROOT / "src" / "posteryard" / "config.py").read_text()
    return set(re.findall(r'"([A-Z][A-Z0-9]*_[A-Z0-9_]+|[A-Z]{4,})"', source)) - set(config.REMOVED_NTFY)


def _readme_defaults() -> dict[str, str]:
    readme = (ROOT / "README.md").read_text()
    table = readme.split("\n## Settings\n", 1)[1].split("\n## ", 1)[0]
    defaults = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        for name in re.findall(r"`([A-Z][A-Z0-9_]+)`", cells[0]):
            defaults[name] = cells[-1].strip("`")
    return defaults


def _options(setting: Setting) -> list[str]:
    return [option[0] for option in setting.get("options", [])]


def test_every_setting_is_on_the_page_or_named_elsewhere() -> None:
    on_page = {s["key"] for s in SETTINGS}
    elsewhere = set(PAGE["elsewhere"])
    assert on_page.isdisjoint(elsewhere)
    assert sorted(_read_names() - on_page - elsewhere) == []
    assert sorted((on_page | elsewhere) - _read_names()) == []


@pytest.mark.parametrize("setting", SETTINGS, ids=lambda s: s["key"])
def test_the_page_default_is_the_service_default(setting: Setting) -> None:
    assert config.load(BASE | {setting["key"]: setting["default"]}) == config.load(BASE)


@pytest.mark.parametrize("setting", SETTINGS, ids=lambda s: s["key"])
def test_every_page_choice_loads(setting: Setting) -> None:
    values = _options(setting) or {"switch": ["true", "false"]}.get(setting["type"], [])
    combos = [",".join(c) for n in range(1, len(values) + 1) for c in itertools.combinations(values, n)]
    for value in combos if setting["type"] == "multi" else values:
        config.load(BASE | {setting["key"]: value})


@pytest.mark.parametrize(("name", "allowed"), CHOICES.items())
def test_the_page_offers_every_value_the_service_takes(name: str, allowed: set[str]) -> None:
    setting = next(s for s in SETTINGS if s["key"] == name)
    assert set(_options(setting)) == allowed


def test_a_multi_choice_minimum_matches_the_service() -> None:
    events = next(s for s in SETTINGS if s["key"] == "NOTIFY_EVENTS")
    assert events["min"] == 1
    assert config.load(BASE | {"NOTIFY_EVENTS": ""}).notify_events == {notify.Event.PROBLEMS}


@pytest.mark.parametrize("setting", SETTINGS, ids=lambda s: s["key"])
def test_the_readme_documents_the_page_setting_with_the_same_default(setting: Setting) -> None:
    assert _readme_defaults().get(setting["key"]) == setting["default"]
