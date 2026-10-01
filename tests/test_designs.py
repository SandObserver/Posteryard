import numpy as np
from PIL import Image, ImageChops

from posteryard.quality import Badge
from posteryard.render import designs
from posteryard.render.layers import cover


def art(size: tuple[int, int] = (2000, 3000), colour: tuple[int, int, int] = (90, 120, 160)) -> Image.Image:
    return Image.new("RGB", size, colour)


def changed_rows(a: Image.Image, b: Image.Image) -> np.ndarray:
    diff = np.asarray(ImageChops.difference(a, b).convert("L"))
    return np.flatnonzero(diff.max(axis=1) > 8)


def test_studio_poster_without_extras_is_the_art() -> None:
    source = art()
    assert (
        ImageChops.difference(designs.studio_poster(source, [], None, None), cover(source, *designs.POSTER)).getbbox()
        is None
    )


def test_corner_block_stays_in_the_top_of_the_poster() -> None:
    source = art()
    plain = cover(source, *designs.POSTER)
    out = designs.studio_poster(source, [Badge.UHD, Badge.DOLBY_ATMOS], "LEAVING IN 3 DAYS", None)
    assert out.size == designs.POSTER
    rows = changed_rows(out, plain)
    assert rows.size and rows.min() < 100


def test_season_number_goes_in_the_bottom_strip() -> None:
    source = art()
    plain = cover(source, *designs.POSTER)
    rows = changed_rows(designs.season_poster(source, 2, None, None), plain)
    assert rows.min() >= designs.POSTER[1] * 0.7
    assert ImageChops.difference(designs.season_poster(source, None, None, None), plain).getbbox() is None


def test_fallback_logo_fits_apples_box() -> None:
    logo = Image.new("RGBA", (800, 100), (255, 255, 255, 255))
    out = designs.fallback_poster(art(colour=(0, 0, 0)), logo, caption=None, badges=[], leaving=None, service=None)
    box = Image.eval(out.convert("L"), lambda v: 255 if v > 200 else 0).getbbox()
    assert box is not None
    w, h = designs.POSTER
    assert box[2] - box[0] <= designs.LOGO_BOX[0] * w + 2
    assert abs((box[1] + box[3]) / 2 - designs.LOGO_CENTRE * h) <= 2


def test_episode_still_keeps_the_top_and_writes_the_bottom() -> None:
    still = Image.new("RGB", (1920, 1080), (40, 60, 80))
    still.paste((200, 50, 50), (0, 0, 1920, 400))
    out = designs.episode_still(still, 2, "Half Loop")
    assert out.size == designs.WIDE
    rows = changed_rows(out, cover(still, *designs.WIDE, (0.5, 0.5)))
    assert rows.min() >= designs.WIDE[1] * 0.68


def test_long_episode_titles_are_shortened() -> None:
    out = designs.episode_still(Image.new("RGB", (1920, 1080)), 5, "word " * 80)
    assert out.size == designs.WIDE
