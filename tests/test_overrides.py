import io
from pathlib import Path

import pytest
from PIL import Image

from posteryard import custom_art
from posteryard.store import Store


def jpeg(colour: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (4000, 6000)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.mark.slow
def test_decode_shrinks_and_rejects_garbage() -> None:
    assert max(custom_art.decode(jpeg()).size) <= 2160
    with pytest.raises(custom_art.ArtError):
        custom_art.decode(b"not an image")


def test_decode_refuses_images_with_too_many_pixels() -> None:
    buffer = io.BytesIO()
    Image.new("L", (8000, 8000)).save(buffer, "PNG")
    with pytest.raises(custom_art.ArtError, match="too large"):
        custom_art.decode(buffer.getvalue())


def test_save_refuses_a_key_that_is_not_a_rating_key(tmp_path: Path) -> None:
    with pytest.raises(custom_art.ArtError):
        custom_art.save(Image.new("RGB", (4, 4)), tmp_path, "../1")


@pytest.mark.parametrize("url", ["ftp://example.org/a.jpg", "file:///etc/passwd", "nope", "http://[bad/a.jpg"])
def test_only_http_addresses(url: str) -> None:
    with pytest.raises(custom_art.ArtError):
        custom_art.from_url(url)


@pytest.mark.slow
def test_saved_art_is_named_by_content(tmp_path: Path) -> None:
    image = custom_art.decode(jpeg())
    first = custom_art.save(image, tmp_path, "7")
    assert first == custom_art.save(image, tmp_path, "7")
    assert first != custom_art.save(custom_art.decode(jpeg((10, 200, 10))), tmp_path, "7")
    assert custom_art.load(custom_art.FILE_PREFIX + str(first)).size == image.size


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


def test_a_file_over_the_limit_is_refused_before_it_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    big = tmp_path / "film.mkv"
    with big.open("wb") as file:
        file.truncate(custom_art.MAX_DOWNLOAD + 1)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("the file was read"))
    with pytest.raises(custom_art.ArtError, match="larger than 40 MB"):
        custom_art.from_file(big)


def test_load_opens_only_saved_jpeg_art(tmp_path: Path) -> None:
    saved = custom_art.save(Image.new("RGB", (20, 30)), tmp_path, "1")
    assert custom_art.load(custom_art.FILE_PREFIX + str(saved)).size == (20, 30)
    other = tmp_path / "other.png"
    Image.new("RGB", (20, 30)).save(other)
    with pytest.raises(OSError, match="cannot identify"):
        custom_art.load(custom_art.FILE_PREFIX + str(other))


def test_a_failed_save_keeps_the_saved_art(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (4, 4), "red")
    path = custom_art.save(image, tmp_path, "7")
    before = path.read_bytes()

    def full(self: Path, data: bytes) -> int:
        with self.open("wb") as file:
            file.write(data[:3])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", full)
    with pytest.raises(OSError):
        custom_art.save(image, tmp_path, "7")
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.part")) == []
