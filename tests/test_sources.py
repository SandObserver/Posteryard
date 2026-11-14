from datetime import date
from pathlib import Path

from posteryard import config
from posteryard.artwork import MemoryChoices
from posteryard.fanart import Fanart, FanartImages
from posteryard.settings import Settings
from posteryard.sources import Sources
from posteryard.store import Store
from posteryard.tmdb import Kind
from tests.fakes import FakeTmdb


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


class CountingFanart(Fanart):
    def __init__(self) -> None:
        super().__init__("example")
        self.calls = 0

    def images(self, kind: Kind, tmdb_id: int, tvdb_id: int | None) -> FanartImages:
        self.calls += 1
        return FanartImages()


def test_fanart_counts_as_reached_only_when_asked(tmp_path: Path) -> None:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path)})
    fanart = CountingFanart()
    sources = Sources(FakeTmdb(), Settings.from_config(cfg, {}, date(2026, 1, 1)), fanart=fanart)
    sources.fanart_images("tv", 1)
    assert (fanart.calls, sources.fanart_reached) == (0, False)
    sources.fanart_images("movie", 1)
    assert (fanart.calls, sources.fanart_reached) == (1, True)
    sources.fanart_reached = False
    sources.fanart_images("movie", 1)
    assert (fanart.calls, sources.fanart_reached) == (1, False)
