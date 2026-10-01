from posteryard.quality import Badge
from posteryard.render.corner import layout

WIDTH = 1000


def test_badges_keep_their_place_when_a_label_is_added() -> None:
    alone = layout([Badge.UHD, Badge.DOLBY_VISION], None, WIDTH)
    with_label = layout([Badge.UHD, Badge.DOLBY_VISION], "LEAVING IN 3 DAYS", WIDTH)
    assert [(p.x, p.y) for p in alone] == [(p.x, p.y) for p in with_label[:2]]


def test_label_sits_under_the_badges_on_the_same_left_edge() -> None:
    placed = layout([Badge.UHD], "LEAVING IN 3 DAYS", WIDTH)
    badge, text = placed
    assert text.text == "LEAVING IN 3 DAYS"
    assert text.x == badge.x
    assert text.y > badge.y + badge.height


def test_label_takes_the_top_row_without_badges() -> None:
    first_badge = layout([Badge.UHD], None, WIDTH)[0]
    label_only = layout([], "LEAVING IN 3 DAYS", WIDTH)[0]
    assert label_only.y <= first_badge.y


def test_nothing_to_place() -> None:
    assert layout([], None, WIDTH) == []
