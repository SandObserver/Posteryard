from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageChops

from posteryard.quality import Badge
from posteryard.render import designs, lines
from posteryard.render.layers import cover


def art(size: tuple[int, int] = (2000, 3000), colour: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    return Image.new("RGB", size, colour)


def logo() -> Image.Image:
    return Image.new("RGBA", (800, 100), (255, 255, 255, 255))


def bright_rows(image: Image.Image) -> np.ndarray:
    grey = np.asarray(image.convert("L"))
    return np.flatnonzero(grey.max(axis=1) > 200)


def tile(colour: tuple[int, int, int] = (0, 0, 0), **kwargs: Any) -> Image.Image:
    kwargs.setdefault("lines_below", [])
    return designs.tile_poster(art(colour=colour), logo(), **kwargs)


def test_logo_bottom_sits_on_apples_line_without_lines() -> None:
    out = tile()
    assert out.size == designs.POSTER
    box = Image.eval(out.convert("L"), lambda v: 255 if v > 200 else 0).getbbox()
    assert box is not None
    w, h = designs.POSTER
    assert box[2] - box[0] <= designs.LOGO_BOX[0] * w + 2
    assert abs(box[3] - lines.LOGO_ALONE * h) <= 2


def test_one_line_lifts_the_logo_to_apples_caption_spot() -> None:
    bottom, centres = lines.stack([lines.Caption("Specials")])
    assert bottom == pytest.approx(0.828)
    assert centres == [lines.LAST_LINE]


def test_more_lines_push_the_logo_up_and_keep_the_order() -> None:
    quality = lines.Badges((Badge.UHD, Badge.DOLBY_VISION))
    access = lines.Badges((Badge.SDH, Badge.CC))
    bottom, centres = lines.stack([quality, access])
    assert centres == sorted(centres)
    assert centres[-1] == lines.LAST_LINE
    assert bottom < lines.stack([quality])[0]
    assert bottom > 0.7


def test_caption_is_translucent() -> None:
    out = tile(lines_below=[lines.Caption("Specials")])
    h = designs.POSTER[1]
    band = np.asarray(out.convert("L"))[round(0.88 * h) : round(0.93 * h)]
    assert 100 < band.max() < 180


def test_a_label_sits_above_the_logo_and_never_moves_it() -> None:
    plain = tile()
    labelled = tile(label=lines.Label("JUST ADDED", (48, 209, 88)))
    h = designs.POSTER[1]
    diff = np.flatnonzero(np.asarray(ImageChops.difference(labelled, plain).convert("L")).max(axis=1) > 8)
    logo_top = round(lines.LOGO_ALONE * h) - round(designs.LOGO_BOX[0] * designs.POSTER[0] / 8)
    assert diff.max() < logo_top
    row = np.asarray(labelled)[round(logo_top - lines.LABEL_ABOVE * h)]
    assert any(r < 80 and g > 180 and b < 120 for r, g, b in row)


def test_wide_badge_rows_shrink_to_fit() -> None:
    kinds = (Badge.UHD, Badge.HDR10_PLUS, Badge.DTS_X, Badge.SURROUND_7_1, Badge.SDH, Badge.CC, Badge.AD)
    out = np.asarray(tile(colour=(0, 0, 0), lines_below=[lines.Badges(kinds)]).convert("L"))
    cols = np.flatnonzero(out[round(0.89 * designs.POSTER[1]) : round(0.92 * designs.POSTER[1])].max(axis=0) > 100)
    assert cols.max() - cols.min() <= lines.MAX_ROW * designs.POSTER[0] + 2


def test_season_number_sits_top_left_and_fades() -> None:
    out = np.asarray(tile(number=4).convert("L"))
    w, h = designs.POSTER
    rows = np.flatnonzero(out[: h // 3, : w // 3].max(axis=1) > 100)
    cols = np.flatnonzero(out[: h // 3, : w // 3].max(axis=0) > 100)
    assert abs(rows.min() - designs.NUMBER_AT[1] * h) <= 2
    assert abs(cols.min() - designs.NUMBER_AT[0] * w) <= 2
    assert abs((rows.max() - rows.min()) - designs.NUMBER_CAP * h) <= 0.01 * h
    assert out[rows.min() + 5, : w // 3].max() > out[rows.max() - 3, : w // 3].max()


def test_episode_still_keeps_the_top_and_writes_the_bottom() -> None:
    still = Image.new("RGB", (1920, 1080), (40, 60, 80))
    still.paste((200, 50, 50), (0, 0, 1920, 400))
    out = designs.episode_still(still, 2, "Half Loop")
    assert out.size == designs.WIDE
    diff = np.asarray(ImageChops.difference(out, cover(still, *designs.WIDE, (0.5, 0.5))).convert("L"))
    assert np.flatnonzero(diff.max(axis=1) > 8).min() >= designs.WIDE[1] * 0.68


def test_long_episode_titles_are_shortened() -> None:
    assert designs.episode_still(Image.new("RGB", (1920, 1080)), 5, "word " * 80).size == designs.WIDE


def test_service_mark_sits_top_left() -> None:
    plain = tile(colour=(90, 90, 90))
    out = tile(colour=(90, 90, 90), service="netflix")
    diff = np.asarray(ImageChops.difference(out, plain).convert("L"))
    cols = np.flatnonzero(diff.max(axis=0) > 8)
    rows = np.flatnonzero(diff.max(axis=1) > 8)
    w, h = designs.POSTER
    assert cols.max() < w / 2
    assert rows.max() < h / 3


def test_stacked_marks_are_taller_than_wide_ones() -> None:
    w = designs.POSTER[0]
    hbo_w, hbo_h = designs._service_size("hbomax", w)
    netflix_w, netflix_h = designs._service_size("netflix", w)
    assert hbo_h > netflix_h
    assert abs(hbo_w * hbo_h - netflix_w * netflix_h) / (netflix_w * netflix_h) < 0.05


def test_service_mark_reaches_contrast_on_light_art() -> None:
    canvas = Image.new("RGBA", designs.POSTER, (245, 245, 245, 255))
    logo = designs._service_logo("hbomax", designs.POSTER[0])
    at = (44, 44)
    assert designs._contrast_behind(canvas, logo, at) < 1.1
    assert designs._contrast_behind(designs._shade_for(canvas, logo, at), logo, at) >= designs.SERVICE_CONTRAST


def test_service_shade_stays_light_on_dark_art() -> None:
    canvas = Image.new("RGBA", designs.POSTER, (20, 20, 20, 255))
    logo = designs._service_logo("netflix", designs.POSTER[0])
    shaded = designs._shade_for(canvas, logo, (44, 44))
    assert designs._contrast_behind(shaded, logo, (44, 44)) >= designs.SERVICE_CONTRAST


def test_text_logo_breaks_long_titles_into_two_lines() -> None:
    one = designs.text_logo("Up")
    two = designs.text_logo("The Very Long Example Title Of A Film")
    assert one.width / one.height > 1
    assert two.height > one.height
    assert designs.tile_poster(art(), two, lines_below=[]).size == designs.POSTER


def test_plain_episode_still_has_no_text() -> None:
    still = Image.new("RGB", (1920, 1080), (200, 200, 200))
    out = np.asarray(designs.episode_still(still, 3, None).convert("L"))
    assert out[: round(0.6 * 1080)].min() > 190
    assert out[-1].max() < out[0].min()


def test_channel_tile_splits_art_and_band() -> None:
    out = np.asarray(designs.channel_tile(art(colour=(200, 200, 200)), logo(), "netflix"))
    h = designs.POSTER[1]
    seam = round(designs.CHANNEL_SEAM * h)
    assert tuple(out[seam + 5, 5]) == pytest.approx(designs.CHANNEL_BANDS["netflix"][0], abs=3)
    assert out[seam - 5, 5].mean() > 150
    band = out[seam:, :, :].max(axis=2)
    rows = np.flatnonzero(band.max(axis=1) > 240)
    assert rows.size and abs((rows.min() + rows.max()) / 2 - (h - seam) / 2) < 0.02 * h


def test_compact_marks_stop_at_the_height_cap() -> None:
    out = np.asarray(designs.channel_tile(art(), None, "appletv").convert("L"))
    seam = round(designs.CHANNEL_SEAM * designs.POSTER[1])
    rows = np.flatnonzero(out[seam:].max(axis=1) > 240)
    assert rows.max() - rows.min() <= designs.CHANNEL_MARK_MAX_HEIGHT * designs.POSTER[1] + 2
