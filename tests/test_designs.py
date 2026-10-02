import numpy as np
from PIL import Image, ImageChops

from posteryard.quality import Badge
from posteryard.render import designs, inforow
from posteryard.render.layers import cover


def art(size: tuple[int, int] = (2000, 3000), colour: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    return Image.new("RGB", size, colour)


def logo() -> Image.Image:
    return Image.new("RGBA", (800, 100), (255, 255, 255, 255))


def bright_rows(image: Image.Image) -> np.ndarray:
    grey = np.asarray(image.convert("L"))
    return np.flatnonzero(grey.max(axis=1) > 200)


def test_logo_fits_apples_box() -> None:
    out = designs.tile_poster(art(), logo(), caption=None, badges=[], leaving=None, service=None)
    assert out.size == designs.POSTER
    box = Image.eval(out.convert("L"), lambda v: 255 if v > 200 else 0).getbbox()
    assert box is not None
    w, h = designs.POSTER
    assert box[2] - box[0] <= designs.LOGO_BOX[0] * w + 2
    assert abs((box[1] + box[3]) / 2 - designs.LOGO_CENTRE * h) <= 2


def test_caption_lifts_the_logo() -> None:
    plain = designs.tile_poster(art(), logo(), caption=None, badges=[], leaving=None, service=None)
    captioned = designs.tile_poster(art(), logo(), caption="Season 2", badges=[], leaving=None, service=None)
    assert bright_rows(captioned).min() < bright_rows(plain).min()


def test_badges_and_label_sit_under_the_logo() -> None:
    plain = designs.tile_poster(art(), logo(), caption=None, badges=[], leaving=None, service=None)
    out = designs.tile_poster(
        art(), logo(), caption=None, badges=[Badge.UHD, Badge.DOLBY_ATMOS], leaving="LEAVING IN 3 DAYS", service=None
    )
    diff = np.asarray(ImageChops.difference(out, plain).convert("L"))
    rows = np.flatnonzero(diff.max(axis=1) > 8)
    assert rows.min() > designs.POSTER[1] * 0.83


def test_badges_keep_their_place_when_a_label_is_added() -> None:
    alone = inforow.layout([Badge.UHD, Badge.DOLBY_VISION], None, designs.POSTER)
    with_label = inforow.layout([Badge.UHD, Badge.DOLBY_VISION], "LEAVING IN 3 DAYS", designs.POSTER)
    assert [(p.x, p.y) for p in alone] == [(p.x, p.y) for p in with_label[:2]]
    label = with_label[2]
    assert label.y + label.height <= with_label[0].y


def test_label_takes_the_badge_spot_without_badges() -> None:
    badge_row = inforow.layout([Badge.UHD], None, designs.POSTER)[0]
    label = inforow.layout([], "LEAVING IN 3 DAYS", designs.POSTER)[0]
    assert abs((label.y + label.height / 2) - (badge_row.y + badge_row.height / 2)) <= 1
    assert inforow.layout([], None, designs.POSTER) == []


def test_episode_still_keeps_the_top_and_writes_the_bottom() -> None:
    still = Image.new("RGB", (1920, 1080), (40, 60, 80))
    still.paste((200, 50, 50), (0, 0, 1920, 400))
    out = designs.episode_still(still, 2, "Half Loop")
    assert out.size == designs.WIDE
    diff = np.asarray(ImageChops.difference(out, cover(still, *designs.WIDE, (0.5, 0.5))).convert("L"))
    assert np.flatnonzero(diff.max(axis=1) > 8).min() >= designs.WIDE[1] * 0.68


def test_long_episode_titles_are_shortened() -> None:
    assert designs.episode_still(Image.new("RGB", (1920, 1080)), 5, "word " * 80).size == designs.WIDE
