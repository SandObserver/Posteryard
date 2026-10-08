from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageChops

from posteryard.quality import Badge
from posteryard.render import category, designs, lines
from posteryard.render.layers import APPLE_GREEN, NEAR_BLACK, cover, family_for, luminance, preferred_family


def art(size: tuple[int, int] = (2000, 3000), colour: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    return Image.new("RGB", size, colour)


def logo() -> Image.Image:
    return Image.new("RGBA", (800, 100), (255, 255, 255, 255))


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


def test_service_mark_has_no_shade_and_takes_the_ink_that_reads() -> None:
    light = Image.new("RGBA", designs.POSTER, (245, 245, 245, 255))
    out = np.asarray(designs.tile_poster(light, logo(), lines_below=[], service="netflix", corner_ink=NEAR_BLACK))
    corner = out[: round(0.2 * designs.POSTER[1]), : round(0.4 * designs.POSTER[0])]
    assert (corner.min(axis=2) < 60).any()
    assert np.median(corner) > 200
    assert designs.corner_dark(light, "mark")
    assert not designs.corner_dark(Image.new("RGB", designs.POSTER, (20, 20, 20)), "mark")


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


def test_dark_logo_reads_only_where_the_logo_sits_on_light_art() -> None:
    light = art(colour=(250, 250, 250))
    assert designs.dark_logo_reads(light, logo(), [])
    assert not designs.dark_logo_reads(art(colour=(20, 20, 20)), logo(), [])
    people = art(colour=(250, 250, 250))
    people.paste((10, 10, 10), (0, 0, 2000, 1500))
    assert designs.dark_logo_reads(people, logo(), [])
    floor = art(colour=(250, 250, 250))
    floor.paste((10, 10, 10), (0, 2730, 2000, 3000))
    assert designs.dark_logo_reads(floor, logo(), [])


def test_dark_ink_needs_the_badges_and_label_to_read_too() -> None:
    floor = art(colour=(250, 250, 250))
    floor.paste((10, 10, 10), (0, 2600, 2000, 3000))
    badges: list[lines.Line] = [lines.Badges((Badge.UHD,))]
    assert not designs.dark_logo_reads(floor, logo(), badges)
    assert designs.dark_logo_reads(art(colour=(250, 250, 250)), logo(), badges)
    sky = art(colour=(250, 250, 250))
    sky.paste((10, 10, 10), (0, 0, 2000, 2270))
    label = lines.Label("JUST ADDED", APPLE_GREEN)
    assert designs.dark_logo_reads(sky, logo(), badges)
    assert not designs.dark_logo_reads(sky, logo(), badges, label)


def test_one_colour_logos_can_be_recoloured() -> None:
    white = logo()
    two = logo()
    two.paste((255, 40, 40, 255), (0, 0, two.width // 2, two.height))
    assert designs.one_colour(white)
    assert not designs.one_colour(two)
    dark = designs.recolour(white, NEAR_BLACK)
    assert dark.getchannel("A").tobytes() == white.convert("RGBA").getchannel("A").tobytes()
    assert dark.convert("RGB").getpixel((dark.width // 2, dark.height // 2)) == NEAR_BLACK


def test_dark_ink_drops_the_fade_and_draws_dark() -> None:
    light = tile(colour=(250, 250, 250), ink=NEAR_BLACK, corner_ink=NEAR_BLACK, number=2, service="netflix")
    pixels = np.asarray(light.convert("L"))
    assert pixels[-5, 5] > 240
    assert pixels.min() < 60


def banded(top: float, bottom: float) -> Image.Image:
    image = art(colour=(10, 10, 10))
    image.paste((240, 240, 240), (0, round(top * 3000), 2000, round(bottom * 3000)))
    return image


def test_dark_art_gets_no_fade() -> None:
    assert designs.fade_strength(art(colour=(20, 20, 20)), logo(), []) == 0.0
    bare = tile(colour=(20, 20, 20), fade=0.0)
    assert np.asarray(bare.convert("RGB"))[-5].max() == 20


def test_the_fade_stays_when_a_line_or_label_would_not_read() -> None:
    caption: list[lines.Line] = [lines.Caption("Season 2")]
    assert designs.fade_strength(banded(0.88, 0.93), logo(), caption) >= 1.0
    assert designs.fade_strength(banded(0.0, 0.0), logo(), caption) == 0.0
    grey = art(colour=(100, 100, 100))
    assert designs.fade_strength(grey, logo(), []) == 0.0
    assert designs.fade_strength(grey, logo(), caption) >= 1.0
    label = lines.Label("JUST ADDED", APPLE_GREEN)
    assert designs.fade_strength(grey, logo(), [], label) >= 1.0
    assert designs.fade_strength(banded(0.79, 0.825), logo(), [], label) >= 1.0
    assert designs.fade_strength(banded(0.79, 0.825), logo(), []) == 0.0


def test_the_fade_deepens_until_a_white_logo_reads() -> None:
    assert designs.fade_strength(art(colour=(255, 255, 255)), logo(), []) >= 1.0
    strong, weak = tile(colour=(255, 255, 255), fade=1.8), tile(colour=(255, 255, 255))
    assert np.asarray(strong.convert("L"))[-200].mean() < np.asarray(weak.convert("L"))[-200].mean()


def test_category_palettes_follow_apple_genres_and_names() -> None:
    assert category.palette_for(" Sci-Fi ") == "sci-fi"
    assert category.palette_for("Drama") == "drama"
    assert category.palette_for("Oscar Winners") == category.palette_for("oscar winners")
    names = [f"Collection {n}" for n in range(60)]
    assert {category.palette_for(n) for n in names} == set(category.SHARED)


def test_category_name_stays_readable_on_white_art() -> None:
    tile = category.category_tile(Image.new("RGB", (600, 900), (255, 255, 255)), "Toy Story Collection")
    assert tile.size == category.POSTER
    behind = np.asarray(tile.crop((60, 1250, 900, 1420)), dtype=np.float32)
    background = behind[behind.min(axis=2) < 235]
    assert float(np.percentile(luminance(background), 90)) <= 1.05 / category.CONTRAST - 0.05


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("Netflix", "Inter"), ("Türk Dizileri", "Inter"), ("Русское кино", "Inter"), ("한국 영화", "Pretendard"),
        ("日本アニメ", "PretendardJP"), ("中国电影", "NotoSansSC"), ("سینمای ایران", "Vazirmatn"),
        ("סרטים ישראליים", "NotoSansHebrew"), ("ภาพยนตร์ไทย", "NotoSansThai"), ("हिंदी फ़िल्में", "NotoSansDevanagari"),
    ],
)  # fmt: skip
def test_text_gets_a_font_that_has_its_letters(text: str, family: str) -> None:
    assert family_for(text) == family


@pytest.mark.parametrize(
    ("text", "language", "countries", "family"),
    [
        ("流浪地球", "zh", ["CN"], "NotoSansSC"), ("臥虎藏龍", "zh", ["HK", "TW"], "NotoSansTC"),
        ("花樣年華", "cn", ["HK"], "NotoSansTC"), ("羅生門", "ja", ["JP"], "PretendardJP"),
        ("千と千尋の神隠し", "zh", ["CN"], "PretendardJP"), ("Parasite", "ko", ["KR"], "Inter"),
    ],
)  # fmt: skip
def test_the_original_language_chooses_between_chinese_and_japanese(
    text: str, language: str, countries: list[str], family: str
) -> None:
    assert family_for(text, preferred_family(language, countries)) == family


@pytest.mark.parametrize(
    ("name", "cut"),
    [
        ("スタジオジブリ長編アニメーション映画作品コレクション", False),
        ("The Lord of the Rings and The Hobbit Extended Middle-earth Saga Collection Box", True),
        ("KEEP_FOREVER", False),
        ("Supercalifragilisticexpialidociousandmoreandmorewords", False),
    ],
)
def test_long_category_names_stay_inside_the_tile(name: str, cut: bool) -> None:
    face, rows = category._layout(name, *category.POSTER)
    assert len(rows) <= category.MAX_ROWS
    assert all(face.getlength(row) <= category.LABEL_WIDTH * category.POSTER[0] for row in rows)
    assert "".join(rows).replace(" ", "").rstrip("…") in name.replace(" ", "")
    assert rows[-1].endswith("…") == cut
    if name == "KEEP_FOREVER":
        assert rows == ["KEEP_FOREVER"]


def test_right_to_left_names_sit_bottom_right() -> None:
    dark = Image.new("RGB", (600, 900), (20, 20, 20))
    ink = [
        np.asarray(category.category_tile(dark, name), dtype=np.float32)[1300:1420].min(axis=2)
        for name in ("Films", "فیلم")
    ]
    left = [float(column[:, :300].max()) for column in ink]
    right = [float(column[:, 700:].max()) for column in ink]
    assert left[0] > 200 and right[0] < 200
    assert right[1] > 200 and left[1] < 200
