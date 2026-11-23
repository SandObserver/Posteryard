"""Read-only checks against a real media server. They never write to it.

POSTERYARD_LIVE=1 uv run --env-file .env pytest -m live --no-cov
"""

import time
from itertools import islice
from typing import Any

import pytest

from posteryard import cli, config
from posteryard.server import Item, MediaServer, is_item_key

pytestmark = pytest.mark.live
SAMPLE = 5


@pytest.fixture
def server(live_env: dict[str, str]) -> MediaServer:
    cfg = config.load(live_env)
    config.require_server(cfg)
    return cli.media_server(cfg)


def libraries(server: MediaServer, kind: str) -> list[Item]:
    found = [section for section in server.sections() if section.get("type") == kind]
    if not found:
        pytest.skip(f"the server has no {kind} library")
    return found


def sample(server: MediaServer, kind: str) -> list[Item]:
    section = libraries(server, kind)[0]
    items = list(islice(server.section_items(str(section["key"]), kind), SAMPLE))
    if not items:
        pytest.skip(f"the first {kind} library is empty")
    return items


def keys(items: list[Any]) -> list[str]:
    return [str(item.get("ratingKey")) for item in items]


def test_the_server_names_itself(server: MediaServer) -> None:
    identity = server.server_id()
    assert identity.startswith(f"{server.name.lower()}:")
    assert len(identity) > len(server.name) + 1


@pytest.mark.parametrize("kind", ["movie", "show"])
def test_library_items_read_back_by_key(server: MediaServer, kind: str) -> None:
    for key in keys(sample(server, kind)):
        assert is_item_key(key)
        item = server.item(key)
        assert item is not None
        assert str(item["ratingKey"]) == key
        assert item.get("title")


def test_a_show_lists_numbered_seasons(server: MediaServer) -> None:
    seasons = server.children(keys(sample(server, "show"))[0])
    assert seasons
    assert all(is_item_key(key) for key in keys(seasons))
    assert all(isinstance(season.get("index"), int) for season in seasons if season.get("index") is not None)


@pytest.mark.parametrize("kind", ["movie", "show"])
def test_recent_changes_are_listed(server: MediaServer, kind: str) -> None:
    section = libraries(server, kind)[0]
    changed = server.changed_since(str(section["key"]), kind, int(time.time()) - 30 * 86400)
    assert all(is_item_key(key) for key in keys(changed))
