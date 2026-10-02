import json
from pathlib import Path

from posteryard import config
from posteryard.service import Service, parse_webhook, related_keys
from posteryard.store import Store


def test_multipart_webhook_payload() -> None:
    payload = {"event": "library.new", "Metadata": {"ratingKey": "7", "type": "episode"}}
    boundary = "XyZ"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload"\r\nContent-Type: application/json\r\n\r\n'
        f"{json.dumps(payload)}\r\n--{boundary}--\r\n"
    ).encode()
    assert parse_webhook(f"multipart/form-data; boundary={boundary}", body) == payload


def test_json_webhook_and_garbage() -> None:
    assert parse_webhook("application/json", b'{"event": "media.play"}') == {"event": "media.play"}
    assert parse_webhook("text/plain", b"hello") is None


def test_an_episode_brings_its_season_and_show() -> None:
    assert related_keys({"ratingKey": 7, "parentRatingKey": 6, "grandparentRatingKey": 5}) == ["7", "6", "5"]
    assert related_keys({"ratingKey": "9"}) == ["9"]


class FakePlex:
    def __init__(self) -> None:
        self.ignored: list[dict[str, str]] = []

    def sections(self) -> list[dict[str, str]]:
        return [{"key": "3", "title": "Movies", "type": "movie"}]

    def section_items(self, section: str, kind: str, **filters: str) -> list[dict[str, str]]:
        if filters.get("label") == "posteryard-next":
            return [{"ratingKey": "9"}]
        if filters.get("label") == "posteryard-ignore":
            return self.ignored
        if filters:
            return []
        return [{"ratingKey": "1"}, {"ratingKey": "2"}]

    def changed_since(self, section: str, kind: str, since: int) -> list[dict[str, str]]:
        return []


def make_service(tmp_path: Path, plex: FakePlex | None = None) -> Service:
    cfg = config.load({"TMDB_API_KEY": "example", "DATA_DIR": str(tmp_path)})
    return Service(cfg, plex or FakePlex(), Store(cfg.state_path), worker=None)  # type: ignore[arg-type]


def test_a_full_pass_stays_pending_until_the_queue_drains(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.full()
    assert service.store.meta("full_pending") == "1"
    service.idle()
    assert service.store.meta("full_pending") == "1"
    while not service.queue.empty():
        key, _ = service.queue.get()
        service._queued.discard(key)
    service.idle()
    assert service.store.meta("full_pending") == "0"


def test_a_restart_resumes_an_unfinished_full_pass(tmp_path: Path) -> None:
    make_service(tmp_path).full()
    restarted = make_service(tmp_path)
    restarted.resume()
    assert restarted.queue.qsize() == 2
    finished = make_service(tmp_path)
    finished.store.set_meta("full_pending", "0")
    finished.resume()
    assert finished.queue.empty()


def test_the_sweep_queues_labelled_items(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.worker = type("W", (), {"leaving_days": staticmethod(lambda: {})})()
    service.sweep()
    queued = [service.queue.get()[0] for _ in range(service.queue.qsize())]
    assert queued == ["9"]


def test_an_item_that_loses_the_ignore_label_is_queued(tmp_path: Path) -> None:
    plex = FakePlex()
    service = make_service(tmp_path, plex)
    service.worker = type("W", (), {"leaving_days": staticmethod(lambda: {})})()
    plex.ignored = [{"ratingKey": "5"}]
    service.sweep()
    assert "5" not in [service.queue.get()[0] for _ in range(service.queue.qsize())]
    plex.ignored = []
    service.sweep()
    assert "5" in [service.queue.get()[0] for _ in range(service.queue.qsize())]
