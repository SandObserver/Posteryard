import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from posteryard import config
from posteryard.store import Store

SETTINGS = sorted(
    set(re.findall(r'"([A-Z][A-Z0-9_]+)"', Path(config.__file__).read_text(encoding="utf-8")))
    | {f"{name}_FILE" for name in config.SECRETS}
)


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def close_stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    opened: list[Store] = []
    original = Store.__init__

    def tracked(self: Store, path: Path) -> None:
        original(self, path)
        opened.append(self)

    monkeypatch.setattr(Store, "__init__", tracked)
    yield
    for store in opened:
        store.close()
