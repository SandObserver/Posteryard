import hashlib
import io
import urllib.parse
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from posteryard import http
from posteryard.render.layers import flatten
from posteryard.server import is_item_key
from posteryard.tmdb import open_image

MAX_DOWNLOAD = 40 * 1024 * 1024
FILE_PREFIX = "file:"


class ArtError(Exception):
    pass


def decode(data: bytes) -> Image.Image:
    if len(data) > MAX_DOWNLOAD:
        raise ArtError("the image is larger than 40 MB")
    try:
        return flatten(open_image(data))
    except ValueError as exc:
        raise ArtError(str(exc)) from None
    except (UnidentifiedImageError, OSError) as exc:
        raise ArtError(f"not a readable image: {exc}") from None


def from_url(url: str) -> Image.Image:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise ArtError("the address is not valid") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ArtError("the address must start with http:// or https://")
    try:
        return decode(http.request("GET", url, timeout=60, retries=2))
    except http.RequestError as exc:
        raise ArtError(f"could not download the image: {exc}") from None


def from_file(path: Path) -> Image.Image:
    if not path.is_file():
        raise ArtError(f"no such file: {path}")
    if path.stat().st_size > MAX_DOWNLOAD:
        raise ArtError("the image is larger than 40 MB")
    return decode(path.read_bytes())


def save(image: Image.Image, data_dir: Path, rating_key: str) -> Path:
    """Store the art under a name that changes with its content, so the fingerprint changes with it."""
    if not is_item_key(rating_key):
        raise ArtError(f"not a Plex rating key: {rating_key!r}")
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=92)
    data = buffer.getvalue()
    folder = data_dir / "custom"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rating_key}-{hashlib.sha256(data).hexdigest()[:12]}.jpg"
    # Do not write in place. The override may already point at this name.
    part = path.with_suffix(".part")
    try:
        part.write_bytes(data)
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
    return path


def load(path: str) -> Image.Image:
    return Image.open(path.removeprefix(FILE_PREFIX), formats=("JPEG",)).convert("RGB")
