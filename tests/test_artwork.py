from collections.abc import Callable

import pytest
from PIL import Image

from posteryard import http
from posteryard.artwork import TEXTLESS_RULE, UNCHECKED, UNSURE_RECHECK, MemoryChoices, Picker, promotion
from posteryard.ocr import TextLine
from posteryard.tmdb import ImageRef, Images

TEXT = {"/a.jpg": "EXAMPLE", "/b.jpg": "", "/c.jpg": "", "/d.jpg": "A BIG TAGLINE"}


def make() -> tuple[Picker, list[str]]:
    reads: list[str] = []

    def fetch(path: str) -> Image.Image:
        image = Image.new("RGBA", (10, 15), (255, 255, 255, 255))
        image.info["path"] = path
        return image

    def read(image: Image.Image) -> list[TextLine]:
        path = str(image.info["path"])
        reads.append(path)
        return [TextLine(TEXT[path], 0.99, 0.06, 0.6)] if TEXT.get(path) else []

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


def test_textless_pool_keeps_every_acceptable_image_in_order() -> None:
    picker, reads = make()
    pool = refs("/a.jpg", "/b.jpg", "/d.jpg", "/c.jpg")
    assert list(picker.textless_pool("tv:1", pool, ["Example"])) == ["/b.jpg", "/c.jpg"]
    assert list(picker.textless_pool("tv:1", pool, ["Example"])) == ["/b.jpg", "/c.jpg"]
    assert len(reads) == 4


def test_textless_pool_reads_only_what_is_used() -> None:
    picker, reads = make()
    pool = refs("/a.jpg", "/b.jpg", "/d.jpg", "/c.jpg")
    assert next(iter(picker.textless_pool("tv:1", pool, ["Example"]))) == "/b.jpg"
    assert reads == ["/a.jpg", "/b.jpg"]
    assert list(picker.textless_pool("tv:1", pool, ["Example"])) == ["/b.jpg", "/c.jpg"]
    assert reads == ["/a.jpg", "/b.jpg", "/d.jpg", "/c.jpg"]


def test_logo_prefers_a_light_wordmark_unless_turned_off() -> None:
    picker, _ = make()
    emblem = ImageRef("/emblem.png", "en", 500, 500, 9, 9)
    wordmark = ImageRef("/wordmark.png", "en", 1000, 300, 5, 1)
    images = Images([], [], [emblem, wordmark])
    assert picker.logo("movie:1", images) == "/wordmark.png"
    assert picker.logo("movie:1", images, prefer_wordmark=False) == "/emblem.png"
    assert picker.logo("movie:2", Images([], [], [])) is None


def test_logo_languages_fall_back_in_order() -> None:
    picker, _ = make()
    images = Images([], [], [ImageRef("/en.png", "en", 1000, 300, 5, 1), ImageRef("/fr.png", "fr", 1000, 300, 5, 1)])
    assert picker.logo("movie:1", images, ("fr", "en")) == "/fr.png"
    assert picker.logo("movie:1", images, ("de", "en")) == "/en.png"


def test_a_colourful_wordmark_beats_a_white_emblem_and_dark_logos_are_skipped() -> None:
    colours = {
        "/red-wide.png": (220, 40, 30, 255),
        "/white-emblem.png": (255, 255, 255, 255),
        "/black-wide.png": (5, 5, 5, 255),
    }
    picker = Picker(MemoryChoices(), lambda path: Image.new("RGBA", (10, 10), colours[path]), lambda image: [])
    black = ImageRef("/black-wide.png", "en", 1000, 300, 9, 9)
    emblem = ImageRef("/white-emblem.png", "en", 500, 500, 8, 8)
    red = ImageRef("/red-wide.png", "en", 1000, 300, 1, 1)
    assert picker.logo("movie:1", Images([], [], [black, emblem, red])) == "/red-wide.png"
    assert picker.logo("movie:2", Images([], [], [black, emblem])) == "/white-emblem.png"


def test_a_white_wordmark_beats_a_coloured_one() -> None:
    colours = {"/red-wide.png": (220, 40, 30, 255), "/white-wide.png": (250, 250, 250, 255)}
    picker = Picker(MemoryChoices(), lambda path: Image.new("RGBA", (10, 10), colours[path]), lambda image: [])
    red = ImageRef("/red-wide.png", "en", 1000, 300, 9, 9)
    white = ImageRef("/white-wide.png", "en", 1000, 300, 1, 1)
    assert picker.logo("movie:1", Images([], [], [red, white])) == "/white-wide.png"


STAN = ["Stanexclusives.", "BETTTTER", "CALL", "au"]
MINIONS = ["ILLUMINATION'S", "minions", "&monsters", "Watch at Home on Digital 8/11", "Also In Theaters"]


def lines(*texts: str) -> list[TextLine]:
    return [TextLine(text, 0.99, 0.2, 0.6) for text in texts]


def promo_picker(read_texts: dict[str, list[str]], cache: MemoryChoices | None = None, prefix: str = "") -> Picker:
    colours = {"/promo.png": (250, 250, 250, 255), "/clean.png": (220, 40, 30, 255), "/other.png": (240, 200, 30, 255)}

    def fetch(path: str) -> Image.Image:
        image = Image.new("RGBA", (40, 20), colours[path])
        image.info["path"] = path
        return image

    def read(image: Image.Image) -> list[TextLine]:
        return lines(*read_texts.get(str(image.info["path"]), []))

    return Picker(cache or MemoryChoices(), fetch, read, prefix)


def test_a_logo_with_promotion_gives_way_to_the_next_logo_in_order() -> None:
    promo = ImageRef("/promo.png", "en", 1000, 400, 0, 0)
    clean = ImageRef("/clean.png", "en", 1000, 400, 9, 8)
    picker = promo_picker({"/promo.png": STAN, "/clean.png": ["BETTER", "CALL", "aul"]})
    assert picker.logo("tv:1", Images([], [], [clean, promo]), titles=["Better Call Saul"]) == "/clean.png"
    assert picker.logo("tv:2", Images([], [], [promo]), titles=["Better Call Saul"]) is None


def test_promotion_is_found_and_title_words_or_studio_lines_are_not() -> None:
    assert promotion(lines(*STAN), ["Better Call Saul"]) == "Stanexclusives."
    assert promotion(lines(*MINIONS), ["Minions & Monsters"]) == "Watch at Home on Digital 8/11"
    assert promotion(lines("ONLY IN THEATERS OCTOBER 2"), ["Verity"]) == "ONLY IN THEATERS OCTOBER 2"
    assert promotion(lines("WATCHMEN", "DC", "A HBOORIGINAL S E RIES"), ["Watchmen"]) is None
    assert promotion(lines("ONLYMURDERS", "IN THE", "BUILDING"), ["Only Murders in the Building"]) is None
    assert promotion(lines("COMING SOON"), ["Coming Soon"]) is None
    assert promotion(lines("MARVELSTUDIOS", "IRON MAN"), ["Iron Man"]) is None
    assert promotion([TextLine("Stanexclusives.", 0.3, 0.2, 0.6)], ["Better Call Saul"]) is None


def test_a_logo_chosen_without_the_text_check_is_not_reused_with_it() -> None:
    cache = MemoryChoices()
    logos = [ImageRef("/promo.png", "en", 1000, 400, 0, 0), ImageRef("/clean.png", "en", 1000, 400, 9, 8)]
    images = Images([], [], logos)
    unchecked = promo_picker({}, cache, UNCHECKED)
    assert unchecked.logo("tv:1", images, titles=["Better Call Saul"]) == "/promo.png"
    checked = promo_picker({"/promo.png": STAN}, cache)
    assert checked.logo("tv:1", images, titles=["Better Call Saul"]) == "/clean.png"


def test_the_pool_passes_over_a_missing_image() -> None:
    picker, reads = make()
    fetch = picker.fetch

    def fetch_some(path: str) -> Image.Image:
        if path == "/c.jpg":
            raise http.HttpError(404, path)
        return fetch(path)

    pool = Picker(picker.cache, fetch_some, picker.read).textless_pool("tv:1", refs("/c.jpg", "/b.jpg"), ["Example"])
    assert list(pool) == ["/b.jpg"]
    assert reads == ["/b.jpg"]


def test_cached_only_downloads_nothing() -> None:
    picker, reads = make()
    assert picker.textless("tv:1", refs("/b.jpg"), ["Example"], cached_only=True) is None
    assert reads == []
    picker.textless("tv:1", refs("/b.jpg"), ["Example"])
    assert picker.textless("tv:1", refs("/b.jpg"), ["Example"], cached_only=True) is not None


def small_title(path: str) -> Image.Image:
    image = Image.new("RGB", (10, 15))
    image.info["path"] = path
    return image


def read_small(reads: list[str]) -> Callable[[Image.Image], list[TextLine]]:
    def read(image: Image.Image) -> list[TextLine]:
        path = str(image.info["path"])
        reads.append(path)
        return [TextLine("DIE SIMPSONS", 0.95, 0.02, 0.2)] if path == "/a.jpg" else []

    return read


def test_a_cached_choice_is_picked_again_when_it_shows_a_new_title() -> None:
    reads: list[str] = []
    picker = Picker(MemoryChoices(), small_title, read_small(reads))
    first = picker.textless("tv:1", refs("/a.jpg", "/b.jpg"), ["The Simpsons"])
    assert first is not None and first.path == "/a.jpg"
    assert picker.textless("tv:1", refs("/a.jpg", "/b.jpg"), ["The Simpsons"]) == first
    assert reads == ["/a.jpg"]
    again = picker.textless("tv:1", refs("/a.jpg", "/b.jpg"), ["Die Simpsons", "The Simpsons"])
    assert again is not None and again.path == "/b.jpg"


def test_a_cached_pool_drops_art_that_shows_a_new_title() -> None:
    reads: list[str] = []
    picker = Picker(MemoryChoices(), small_title, read_small(reads))
    assert list(picker.textless_pool("tv:1", refs("/a.jpg", "/b.jpg"), ["The Simpsons"])) == ["/a.jpg", "/b.jpg"]
    pool = picker.textless_pool("tv:1", refs("/a.jpg", "/b.jpg"), ["Die Simpsons", "The Simpsons"])
    assert list(pool) == ["/b.jpg"]
    assert reads == ["/a.jpg", "/b.jpg"]


def test_a_pool_cached_without_its_text_is_trusted() -> None:
    reads: list[str] = []
    cache = MemoryChoices()
    picker = Picker(cache, small_title, read_small(reads))
    key = f"textless-all{TEXTLESS_RULE}:tv:1"
    cache.put_choice(key, {"candidates": ["/a.jpg", "/b.jpg"], "verdicts": {"/a.jpg": True, "/b.jpg": True}})
    pool = picker.textless_pool("tv:1", refs("/a.jpg", "/b.jpg"), ["Die Simpsons"])
    assert list(pool) == ["/a.jpg", "/b.jpg"]
    assert reads == []


def test_art_of_an_odd_shape_is_not_read() -> None:
    picker, reads = make()
    fetch = picker.fetch

    def fetch_some(path: str) -> Image.Image:
        return Image.new("RGB", (1, 2160)) if path == "/c.jpg" else fetch(path)

    picked = Picker(picker.cache, fetch_some, picker.read).textless("tv:1", refs("/c.jpg", "/b.jpg"), ["Example"])
    assert picked is not None and picked.path == "/b.jpg"
    assert reads == ["/b.jpg"]


def test_a_gone_or_unreadable_logo_is_passed_over() -> None:
    def fetch(path: str) -> Image.Image:
        if path == "/gone.png":
            raise http.HttpError(404, path)
        if path == "/broken.png":
            raise OSError("not a readable image")
        return Image.new("RGBA", (10, 10), (250, 250, 250, 255))

    picker = Picker(MemoryChoices(), fetch, lambda image: [])
    logos = [ImageRef(p, "en", 1000, 300, 9 - i, 1) for i, p in enumerate(("/gone.png", "/broken.png", "/fine.png"))]
    assert picker.logo("movie:1", Images([], [], logos)) == "/fine.png"
    assert picker.logo("movie:2", Images([], [], logos[:2])) is None


def test_a_block_is_not_stored_as_no_art() -> None:
    blocked = [True]

    def fetch(path: str) -> Image.Image:
        if blocked[0]:
            raise http.HttpError(403, path)
        image = Image.new("RGBA", (10, 15), (255, 255, 255, 255))
        image.info["path"] = path
        return image

    picker = Picker(MemoryChoices(), fetch, lambda image: [])
    candidates = refs("/b.jpg")
    with pytest.raises(http.HttpError):
        picker.textless("movie:1", candidates, ["Example"])
    blocked[0] = False
    assert picker.textless("movie:1", candidates, ["Example"]) is not None


def test_a_pick_past_an_unreadable_candidate_is_stored_for_a_few_days(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def fetch(path: str) -> Image.Image:
        fetched.append(path)
        if path == "/broken.jpg":
            raise OSError("cannot identify image file")
        image = Image.new("RGBA", (10, 15), (255, 255, 255, 255))
        image.info["path"] = path
        return image

    now = [1000.0]
    monkeypatch.setattr("posteryard.artwork.time.time", lambda: now[0])
    picker = Picker(MemoryChoices(), fetch, lambda image: [])
    candidates = refs("/broken.jpg", "/good.jpg")
    for _ in range(3):
        picked = picker.textless("movie:1", candidates, ["Example"])
        assert picked is not None and picked.path == "/good.jpg"
    assert fetched == ["/broken.jpg", "/good.jpg"]
    now[0] += UNSURE_RECHECK
    picker.textless("movie:1", candidates, ["Example"])
    assert fetched.count("/broken.jpg") == 2


def test_a_logo_picked_past_an_unreadable_candidate_is_stored_for_a_few_days(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def fetch(path: str) -> Image.Image:
        fetched.append(path)
        if path == "/broken.png":
            raise OSError("cannot identify image file")
        return Image.new("RGBA", (400, 100), (255, 255, 255, 255))

    now = [1000.0]
    monkeypatch.setattr("posteryard.artwork.time.time", lambda: now[0])
    picker = Picker(MemoryChoices(), fetch, lambda image: [])
    logos = Images([], [], [ImageRef(p, "en", 400, 100, 5, 1) for p in ("/broken.png", "/good.png")])
    for _ in range(3):
        assert picker.logo("movie:1", logos) == "/good.png"
    assert fetched.count("/broken.png") == 1
    now[0] += UNSURE_RECHECK
    assert picker.logo("movie:1", logos) == "/good.png"
    assert fetched.count("/broken.png") == 2


def test_a_page_that_is_not_an_image_is_not_stored_as_no_art() -> None:
    filtered = [True]

    def fetch(path: str) -> Image.Image:
        if filtered[0]:
            raise OSError("cannot identify image file")
        image = Image.new("RGBA", (10, 15), (255, 255, 255, 255))
        image.info["path"] = path
        return image

    picker = Picker(MemoryChoices(), fetch, lambda image: [])
    candidates = refs("/b.jpg")
    logos = [ImageRef("/l.png", "en", 1000, 300, 9, 1)]
    assert picker.textless("movie:1", candidates, ["Example"]) is None
    assert picker.logo("movie:1", Images([], [], logos)) is None
    assert list(picker.textless_pool("movie:1", candidates, ["Example"])) == []
    filtered[0] = False
    assert picker.textless("movie:1", candidates, ["Example"]) is not None
    assert picker.logo("movie:1", Images([], [], logos)) == "/l.png"
    assert list(picker.textless_pool("movie:1", candidates, ["Example"])) == ["/b.jpg"]
