import hashlib
import json
import threading
import urllib.parse
from collections.abc import Callable, Iterable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pytest
from PIL import Image, ImageDraw

from posteryard import cli, http, pipeline
from posteryard.config import Config
from posteryard.notify import Notifier
from posteryard.ocr import TextLine
from posteryard.quality import QualityMinimums
from posteryard.server import Item, MediaServer, Target
from posteryard.settings import Settings
from posteryard.sources import Sources
from posteryard.store import Store
from posteryard.tmdb import Details, Episode, ImageRef, Images, Kind, RegionOffers, Tmdb
from posteryard.worker import Outcome, Restored, Worker


def ref(path: str, language: str | None) -> ImageRef:
    return ImageRef(path, language, 2000, 3000, 5.0, 10)


class FakeServer(MediaServer):
    """A media server with no items. Subclasses override what a test needs."""

    name = "Plex"
    url = "http://plex.example:32400"
    collections_per_library = True
    has_guide = False

    def server_id(self) -> str:
        return "plex:example"

    def sections(self) -> list[Item]:
        return []

    def item(self, rating_key: str) -> Item | None:
        return None

    def children(self, rating_key: str) -> list[Item]:
        return []

    def section_items(self, section_key: str, kind: str, *, label: str | None = None) -> Iterable[Item]:
        return []

    def changed_since(self, section_key: str, kind: str, since: int) -> list[Item]:
        return []

    def newest_added(
        self, section_key: str, kind: str, *, show: str | None = None, season: str | None = None
    ) -> int | None:
        return None

    def selected(self, rating_key: str, target: Target) -> str | None:
        return None

    def check_upload(self, rating_key: str, target: Target) -> None:
        pass

    def upload(self, rating_key: str, target: Target, jpeg: bytes) -> str:
        return f"upload://posters/{rating_key}-{target}"

    def poster_bytes(self, item: Item) -> bytes:
        return b""

    def remove_label(self, item: Item, label: str) -> None:
        pass

    def still_ours(self, item: Item, target: Target) -> bool:
        return False

    def lock(self, item: Item, target: Target) -> None:
        pass

    def restore(self, item: Item, target: Target, original: str | None = None) -> None:
        pass

    def collection_children(self, rating_key: str) -> list[Item]:
        return []

    def create_collection(self, section_key: str, kind: str, title: str, rating_keys: list[str]) -> str:
        return "60"

    def add_to_collection(self, rating_key: str, members: list[str]) -> None:
        pass

    def remove_from_collection(self, rating_key: str, member: str) -> None:
        pass

    def delete_collection(self, rating_key: str) -> None:
        pass

    def set_label(self, section_key: str, kind: str, rating_key: str, label: str) -> None:
        pass


class FakeTmdb(Tmdb):
    """A show with seasons 1 to 4. Seasons 1 and 3 have their own textless art."""

    def __init__(self, posters: list[ImageRef] | None = None, seasons: int = 4) -> None:
        super().__init__("example")
        self.posters = posters or []
        self.seasons = seasons
        self.backdrops = [
            ImageRef("/backdrop.jpg", None, 3840, 2160, 5, 10),
            ImageRef("/backdrop2.jpg", None, 3840, 2160, 4, 1),
        ]
        self.logos = [ref("/logo.png", "en")]
        self.finds: list[str] = []
        self.episode_ids: dict[str, dict[str, int]] = {}

    def details(self, kind: Kind, tmdb_id: int) -> Details:
        return {"title": "Example Movie", "seasons": [{"season_number": n} for n in range(1, self.seasons + 1)]}

    def images(self, kind: Kind, tmdb_id: int) -> Images:
        return Images(self.posters, self.backdrops, self.logos)

    def season_images(self, show_id: int, season: int) -> Images:
        return Images([ref(f"/season{season}.jpg", None)] if season in (1, 3) else [], [], [])

    def all_titles(self, kind: Kind, tmdb_id: int) -> list[str]:
        return ["Example Movie", "Película de Ejemplo"]

    def watch_providers(self, kind: Kind, tmdb_id: int) -> dict[str, RegionOffers]:
        return {}

    def episode(self, show_id: int, season: int, episode: int) -> Episode | None:
        return {"still_path": f"/still-{season}-{episode}.jpg", "name": f"Episode {season}x{episode}"}

    def find_episode(self, source: str, external_id: str) -> dict[str, int] | None:
        self.finds.append(external_id)
        return self.episode_ids.get(external_id)

    def find(self, source: str, external_id: str) -> dict[str, int]:
        self.finds.append(external_id)
        return {"movie": 77} if external_id == "tt0000077" else {}


def worker(cfg: Config, server: MediaServer, store: Store, notifier: Notifier | None = None) -> Worker:
    return Worker(cfg, server, store, notifier or Notifier(), FakeTmdb())


TEXT = {"/foreign.jpg": "PELICULA DE EJEMPLO", "/english.jpg": "EXAMPLE MOVIE"}


def fetch(path: str) -> Image.Image:
    if path.endswith(".png"):
        logo = Image.new("RGBA", (600, 120), (0, 0, 0, 0))
        ImageDraw.Draw(logo).rectangle((0, 0, 599, 119), fill=(255, 255, 255, 255))
        return logo
    noise = np.random.default_rng(int(hashlib.sha256(path.encode()).hexdigest()[:8], 16)).integers(0, 160, (8, 9))
    image = Image.fromarray(noise.astype(np.uint8)).convert("RGB").resize((200, 300), Image.Resampling.NEAREST)
    image.info["path"] = path
    return image


def read(image: Image.Image) -> list[TextLine]:
    text = TEXT.get(str(image.info.get("path")), "")
    return [TextLine(text, 0.99, 0.06, 0.6)] if text else []


def context(posters: list[ImageRef], seasons: int = 4) -> pipeline.Context:
    settings = Settings(QualityMinimums(), ("CA",), {"1": date(2026, 10, 4)}, date(2026, 10, 1))
    return pipeline.Context(settings, Sources(FakeTmdb(posters, seasons), settings, read=read, fetch=fetch))


def tmdb_of(ctx: pipeline.Context) -> FakeTmdb:
    assert isinstance(ctx.sources.tmdb, FakeTmdb)
    return ctx.sources.tmdb


APPLE_URL = "https://is1-ssl.mzstatic.com/image/thumb/Features/v4/ab/cd/art.jpg/1680x3636nr.jpg"


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        pass


class LocalHttpServer:
    """A local HTTP server that answers each request with the next queued (status, headers, body). Status 0 hangs up."""

    def __init__(self) -> None:
        self.answers: list[tuple[int, dict[str, str], bytes]] = []
        self.connections: set[int] = set()
        self.requests: list[tuple[str, dict[str, str]]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                server.connections.add(self.client_address[1])
                server.requests.append((self.path, dict(self.headers)))
                status, headers, body = server.answers.pop(0)
                if not status:
                    self.close_connection = True
                    return
                self.send_response(status)
                for name, value in {"Content-Length": str(len(body)), **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        self.httpd = QuietServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def open_idle() -> int:
    count = 0
    for manager in (http.POOL, http.UNTRUSTED_POOL):
        keys = manager.pools.keys()
        for key in keys:
            pool = manager.pools.get(key)
            idle = pool.pool if pool else None
            count += sum(conn is not None and conn.sock is not None for conn in (idle.queue if idle else []))
    return count


class MediaBrowserRoutes:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> bytes:
        parts = urllib.parse.urlsplit(url)
        assert kwargs["headers"]["Authorization"] == 'MediaBrowser Token="example-key"'
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((method, parts.path, query, kwargs.get("data")))
        answer = self.routes.get(f"{method} {parts.path}", self.routes.get(parts.path))
        if callable(answer):
            answer = answer(query)
        if answer is None:
            raise http.HttpError(404, url)
        return answer if isinstance(answer, bytes) else json.dumps(answer).encode()


class LookupPlex(FakeServer):
    identity = "plex:example"

    def server_id(self) -> str:
        return self.identity

    def __init__(self) -> None:
        self.titles: dict[str, list[Item]] = {
            "movie": [
                {"ratingKey": "4411", "title": "Dune", "year": 1984, "type": "movie"},
                {"ratingKey": "8120", "title": "Dune", "year": 2021, "type": "movie"},
                {"ratingKey": "300", "title": "1917", "year": 2019, "type": "movie"},
                {"ratingKey": "301", "title": "Blade Runner 2049", "year": 2017, "type": "movie"},
                {"ratingKey": "302", "title": "Thunderbolts*", "year": 2025, "type": "movie"},
            ],
            "show": [{"ratingKey": "5646", "title": "The Office", "year": 2005, "type": "show"}],
        }
        self.keys: dict[str, Item] = {"1917": {"ratingKey": "1917", "title": "Pilot", "type": "episode"}}

    def sections(self) -> list[Item]:
        return [
            {"key": "3", "title": "Movies", "type": "movie"},
            {"key": "4", "title": "TV Shows", "type": "show"},
            {"key": "5", "title": "Music", "type": "artist"},
        ]

    def section_items(self, section_key: str, kind: str, *, label: str | None = None) -> list[Item]:
        return self.titles[kind]

    def item(self, rating_key: str) -> Item | None:
        every = [*self.titles["movie"], *self.titles["show"]]
        return self.keys.get(rating_key) or next((i for i in every if i["ratingKey"] == rating_key), None)

    def children(self, rating_key: str) -> list[Item]:
        return [
            {"ratingKey": "5647", "index": 1, "title": "Season 1"},
            {"ratingKey": "5648", "index": 2, "title": "Season 2"},
        ]


CLI_ENV = {"TMDB_API_KEY": "example", "PLEX_URL": "http://plex.example:32400", "PLEX_TOKEN": "example"}


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


def cli_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], int]:
    for name, value in {**CLI_ENV, "DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "Plex", lambda url, token: LookupPlex())
    monkeypatch.setattr(cli, "Worker", FakeWorker)
    return cli.main


def detailed(seed: int, size: tuple[int, int] = (600, 900)) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", size, tuple(int(c) for c in rng.integers(0, 120, 3)))
    draw = ImageDraw.Draw(image)
    for _ in range(400):
        x, y = int(rng.integers(0, size[0])), int(rng.integers(0, size[1]))
        w, h = int(rng.integers(6, 40)), int(rng.integers(6, 40))
        draw.rectangle((x, y, x + w, y + h), fill=tuple(int(c) for c in rng.integers(0, 256, 3)))
    return image


class Alerts(Notifier):
    def __init__(self) -> None:
        super().__init__()
        self.subjects: list[str] = []
        self.messages: list[str] = []

    def alert(self, subject: str, message: str) -> None:
        self.subjects.append(subject)
        self.messages.append(message)
