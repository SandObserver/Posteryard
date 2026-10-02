import io
from pathlib import Path

import pytest
from PIL import Image

from posteryard import overrides
from posteryard.store import Store


def jpeg(colour: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (4000, 6000)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "JPEG")
    return buffer.getvalue()


def test_decode_shrinks_and_rejects_garbage() -> None:
    assert max(overrides.decode(jpeg()).size) <= 2160
    with pytest.raises(overrides.ArtError):
        overrides.decode(b"not an image")


@pytest.mark.parametrize("url", ["ftp://example.org/a.jpg", "file:///etc/passwd", "http://127.0.0.1/a.jpg", "nope"])
def test_only_remote_http_addresses(url: str) -> None:
    with pytest.raises(overrides.ArtError):
        overrides.from_url(url)


def test_saved_art_is_named_by_content(tmp_path: Path) -> None:
    image = overrides.decode(jpeg())
    first = overrides.save(image, tmp_path, "7")
    assert first == overrides.save(image, tmp_path, "7")
    assert first != overrides.save(overrides.decode(jpeg((10, 200, 10))), tmp_path, "7")
    assert overrides.load(overrides.FILE_PREFIX + str(first)).size == image.size


def test_store_keeps_one_override_per_title(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    assert store.override("1") is None
    store.add_skip("1", "/a.jpg")
    store.add_skip("1", "/b.jpg")
    override = store.override("1")
    assert override is not None and override.skip == {"/a.jpg", "/b.jpg"} and override.custom is None
    store.set_custom("1", "/data/custom/1-abc.jpg", "plex")
    override = store.override("1")
    assert override is not None and override.custom == "/data/custom/1-abc.jpg" and override.skip == frozenset()
    store.forget("1")
    assert store.override("1") is not None
    store.reset_override("1")
    assert store.override("1") is None
