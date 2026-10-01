"""Choose source art from TMDB for each design."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image

from posteryard import ocr
from posteryard.render.layers import is_light, trim
from posteryard.tmdb import ImageRef, Images

MAX_CANDIDATES = 6
MIN_BACKDROP_WIDTH = 1920

Fetch = Callable[[str], Image.Image]
Read = Callable[[Image.Image], list[ocr.TextLine]]


@dataclass(frozen=True)
class Choice:
    ref: ImageRef
    image: Image.Image
    lines: list[ocr.TextLine]


def titled_poster(refs: Sequence[ImageRef], titles: Sequence[str], fetch: Fetch, read: Read) -> Choice | None:
    """Do not trust TMDB's language tag alone. It lets foreign-language posters through."""
    for ref in refs[:MAX_CANDIDATES]:
        image = fetch(ref.path)
        lines = read(image)
        if ocr.shows_title(lines, titles):
            return Choice(ref, image, lines)
    return None


def textless(refs: Sequence[ImageRef], titles: Sequence[str], fetch: Fetch, read: Read) -> Choice | None:
    """Reject any art that OCR finds a title or display text on, whatever its language tag says."""
    for ref in refs[:MAX_CANDIDATES]:
        image = fetch(ref.path)
        lines = read(image)
        if not ocr.shows_title(lines, titles) and not ocr.has_display_text(lines):
            return Choice(ref, image, lines)
    return None


def textless_art(images: Images, titles: Sequence[str], fetch: Fetch, read: Read) -> Choice | None:
    return textless(images.textless_posters(), titles, fetch, read) or textless(
        images.textless_backdrops(), titles, fetch, read
    )


def background(images: Images, titles: Sequence[str], fetch: Fetch, read: Read) -> Choice | None:
    refs = sorted(images.textless_backdrops(), key=lambda r: r.width < MIN_BACKDROP_WIDTH)
    return textless(refs, titles, fetch, read)


def logo(images: Images, fetch: Fetch) -> Image.Image | None:
    logos = [trim(fetch(ref.path)) for ref in images.english_logos()[:MAX_CANDIDATES]]
    return next((lg for lg in logos if is_light(lg)), logos[0] if logos else None)
