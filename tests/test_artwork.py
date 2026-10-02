from PIL import Image

from posteryard.artwork import MemoryChoices, Picker
from posteryard.ocr import TextLine
from posteryard.tmdb import ImageRef

TEXT = {"/a.jpg": "EXAMPLE", "/b.jpg": "", "/c.jpg": "", "/d.jpg": "A BIG TAGLINE"}


def make() -> tuple[Picker, list[str]]:
    reads: list[str] = []

    def fetch(path: str) -> Image.Image:
        image = Image.new("RGB", (10, 15))
        image.info["path"] = path
        return image

    def read(image: Image.Image) -> list[TextLine]:
        path = str(image.info["path"])
        reads.append(path)
        return [TextLine(TEXT[path], 0.99, 0.06, 0.6)] if TEXT[path] else []

    return Picker(MemoryChoices(), fetch, read), reads


def refs(*paths: str) -> list[ImageRef]:
    return [ImageRef(p, None, 2000, 3000, 5, 1) for p in paths]


def test_textless_skips_titles_and_display_text_and_caches() -> None:
    picker, reads = make()
    first = picker.textless("tv:1", refs("/a.jpg", "/b.jpg"), ["Example"])
    assert first is not None and first.path == "/b.jpg"
    assert picker.textless("tv:1", refs("/a.jpg", "/b.jpg"), ["Example"]) == first
    assert reads == ["/a.jpg", "/b.jpg"]
    picker.textless("tv:1", refs("/c.jpg", "/a.jpg", "/b.jpg"), ["Example"])
    assert reads[2:] == ["/c.jpg"]


def test_textless_all_keeps_every_acceptable_image_in_order() -> None:
    picker, reads = make()
    assert picker.textless_all("tv:1", refs("/a.jpg", "/b.jpg", "/d.jpg", "/c.jpg"), ["Example"]) == [
        "/b.jpg",
        "/c.jpg",
    ]
    picker.textless_all("tv:1", refs("/a.jpg", "/b.jpg", "/d.jpg", "/c.jpg"), ["Example"])
    assert len(reads) == 4
