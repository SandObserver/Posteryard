from PIL import Image

from posteryard.artwork import MemoryChoices, Picker
from posteryard.ocr import TextLine
from posteryard.tmdb import ImageRef


def test_ocr_runs_again_only_when_the_candidates_change() -> None:
    reads: list[str] = []

    def fetch(path: str) -> Image.Image:
        image = Image.new("RGB", (10, 15))
        image.info["path"] = path
        return image

    def read(image: Image.Image) -> list[TextLine]:
        reads.append(str(image.info["path"]))
        return [TextLine("EXAMPLE", 0.99, 0.06, 0.6)] if image.info["path"] == "/b.jpg" else []

    picker = Picker(MemoryChoices(), fetch, read)
    refs = [ImageRef("/a.jpg", "en", 2000, 3000, 5, 1), ImageRef("/b.jpg", "en", 2000, 3000, 4, 1)]
    first = picker.titled("movie:1", refs, ["Example"])
    assert first is not None and first.path == "/b.jpg"
    assert reads == ["/a.jpg", "/b.jpg"]
    again = picker.titled("movie:1", refs, ["Example"])
    assert again is not None and again.path == "/b.jpg" and again.lines[0].text == "EXAMPLE"
    assert reads == ["/a.jpg", "/b.jpg"]
    picker.titled("movie:1", [ImageRef("/c.jpg", "en", 2000, 3000, 6, 1), *refs], ["Example"])
    assert reads[2:] == ["/c.jpg", "/a.jpg", "/b.jpg"]
