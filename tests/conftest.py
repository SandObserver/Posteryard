from collections.abc import Iterator
from pathlib import Path

import pytest

from posteryard.store import Store


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
