from datetime import date
from pathlib import Path

from posteryard import config
from posteryard.artwork import MemoryChoices
from posteryard.settings import Settings
from posteryard.sources import Sources
from posteryard.store import Store


def test_sources_from_config_take_the_store_and_the_mark_switch(tmp_path: Path) -> None:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path), "FANART_API_KEY": "example"})
    settings = Settings.from_config(cfg, {}, date(2026, 1, 1))
    store = Store(tmp_path / "state.db")
    try:
        stored = Sources.from_config(cfg, settings, state=store)
        assert stored.choices is store and stored.marks is not None and stored.marks.choices is store
        assert stored.fanart is not None
        assert stored.overrides("1") is None
    finally:
        store.close()
    memory = Sources.from_config(cfg, settings)
    assert isinstance(memory.choices, MemoryChoices) and memory.marks is not None
    assert Sources.from_config(cfg, settings, marks=False).marks is None
