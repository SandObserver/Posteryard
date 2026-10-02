"""Per-title art overrides: custom art supplied by the user, or images to skip."""

import hashlib
import io
import ipaddress
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from posteryard import http
from posteryard.tmdb import MAX_SIDE

CUSTOM_LABEL = "posteryard-custom"
NEXT_LABEL = "posteryard-next"
IGNORE_LABEL = "posteryard-ignore"
MAX_DOWNLOAD = 40 * 1024 * 1024
FILE_PREFIX = "file:"


class ArtError(Exception):
    pass


@dataclass(frozen=True)
class Override:
    custom: str | None = None
    source: str = ""
    skip: frozenset[str] = field(default_factory=frozenset)


def decode(data: bytes) -> Image.Image:
    if len(data) > MAX_DOWNLOAD:
        raise ArtError("the image is larger than 40 MB")
    try:
        source = Image.open(io.BytesIO(data))
        source.draft("RGB", (MAX_SIDE, MAX_SIDE))
        image = source.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ArtError(f"not a readable image: {exc}") from None
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return image


def from_url(url: str) -> Image.Image:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ArtError("the address must start with http:// or https://")
    try:
        if ipaddress.ip_address(parts.hostname).is_loopback:
            raise ArtError("loopback addresses are not allowed")
    except ValueError:
        pass
    try:
        return decode(http.request("GET", url, timeout=60, retries=2))
    except http.RequestError as exc:
        raise ArtError(f"could not download the image: {exc}") from None


def from_file(path: Path) -> Image.Image:
    if not path.is_file():
        raise ArtError(f"no such file: {path}")
    return decode(path.read_bytes())


def save(image: Image.Image, data_dir: Path, rating_key: str) -> Path:
    """Store the art under a name that changes with its content, so the fingerprint changes with it."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=92)
    data = buffer.getvalue()
    folder = data_dir / "custom"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rating_key}-{hashlib.sha256(data).hexdigest()[:12]}.jpg"
    path.write_bytes(data)
    return path


def load(path: str) -> Image.Image:
    return Image.open(path.removeprefix(FILE_PREFIX)).convert("RGB")
