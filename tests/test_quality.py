from posteryard.quality import (
    AudioLevel,
    Badge,
    HdrLevel,
    QualityMinimums,
    VideoLevel,
    accessibility,
    analyse,
    badges,
    best,
)


def media(
    resolution: str = "1080",
    video: dict[str, object] | None = None,
    audio: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    streams = [{"streamType": 1, **(video or {})}, {"streamType": 2, **(audio or {})}]
    return {"videoResolution": resolution, "Part": [{"Stream": streams}], **extra}


def test_uhd_dolby_vision_atmos() -> None:
    q = analyse(media("4k", {"DOVIPresent": True}, {"channels": 8, "displayTitle": "English (TrueHD 7.1 Atmos)"}))
    assert badges(q, QualityMinimums()) == [Badge.UHD, Badge.DOLBY_VISION, Badge.DOLBY_ATMOS]


def test_default_minimums_hide_full_hd_sdr_and_surround() -> None:
    q = analyse(media("1080", {}, {"channels": 6}))
    assert badges(q, QualityMinimums()) == []


def test_lower_minimums_show_hd_and_surround() -> None:
    q = analyse(media("1080", {}, {"channels": 6}))
    minimums = QualityMinimums(VideoLevel.FULL_HD, HdrLevel.HDR10, AudioLevel.SURROUND_5_1)
    assert badges(q, minimums) == [Badge.HD, Badge.SURROUND_5_1]


def test_pq_transfer_is_hdr10_and_title_marks_hdr10_plus() -> None:
    assert analyse(media("4k", {"colorTrc": "smpte2084"})).hdr == HdrLevel.HDR10
    assert analyse(media("4k", {"displayTitle": "4K HDR10+ (HEVC Main 10)"})).hdr == HdrLevel.HDR10_PLUS


def test_dts_x_counts_as_object_audio() -> None:
    q = analyse(media("4k", {}, {"channels": 8, "displayTitle": "English (DTS:X 7.1)"}))
    assert badges(q, QualityMinimums()) == [Badge.UHD, Badge.DTS_X]


def test_off_hides_an_axis() -> None:
    q = analyse(media("4k", {"DOVIPresent": True}, {"displayTitle": "Atmos"}))
    assert badges(q, QualityMinimums(VideoLevel.OFF, HdrLevel.OFF, AudioLevel.ATMOS)) == [Badge.DOLBY_ATMOS]


def test_best_picks_the_highest_version() -> None:
    assert best([media("1080"), media("4k")]).video == VideoLevel.UHD
    assert best([]).video == VideoLevel.OFF


def test_accessibility_from_flags_and_titles() -> None:
    every = frozenset({Badge.SDH, Badge.CC, Badge.AD})
    flagged = [
        {"Part": [{"Stream": [{"streamType": 3, "hearingImpaired": True}, {"streamType": 2, "visualImpaired": True}]}]}
    ]
    assert accessibility(flagged, every) == [Badge.SDH, Badge.AD]
    titled = [
        {"Part": [{"Stream": [{"streamType": 3, "title": "English (SDH)"}, {"streamType": 3, "codec": "eia_608"}]}]}
    ]
    assert accessibility(titled, every) == [Badge.SDH, Badge.CC]
    assert accessibility(titled, frozenset({Badge.CC})) == [Badge.CC]
    plain = [
        {"Part": [{"Stream": [{"streamType": 3, "title": "Latin American"}, {"streamType": 2, "title": "Broadcast"}]}]}
    ]
    assert accessibility(plain, every) == []
