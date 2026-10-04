import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class VideoLevel(StrEnum):
    OFF = "off"
    HD = "720"
    FULL_HD = "1080"
    UHD = "2160"


class HdrLevel(StrEnum):
    OFF = "off"
    HDR10 = "hdr10"
    HDR10_PLUS = "hdr10plus"
    DOLBY_VISION = "dolbyvision"


class AudioLevel(StrEnum):
    OFF = "off"
    SURROUND_5_1 = "5.1"
    SURROUND_7_1 = "7.1"
    ATMOS = "atmos"


class Badge(StrEnum):
    UHD = "4k"
    HD = "hd"
    HDR10 = "hdr10"
    HDR10_PLUS = "hdr10plus"
    DOLBY_VISION = "dolbyvision"
    DOLBY_ATMOS = "dolbyatmos"
    DTS_X = "dtsx"
    SURROUND_7_1 = "7.1"
    SURROUND_5_1 = "5.1"
    SDH = "sdh"
    CC = "cc"
    AD = "ad"


VIDEO_RANK = {VideoLevel.OFF: 0, VideoLevel.HD: 1, VideoLevel.FULL_HD: 2, VideoLevel.UHD: 3}
HDR_RANK = {HdrLevel.OFF: 0, HdrLevel.HDR10: 1, HdrLevel.HDR10_PLUS: 2, HdrLevel.DOLBY_VISION: 3}
AUDIO_RANK = {AudioLevel.OFF: 0, AudioLevel.SURROUND_5_1: 1, AudioLevel.SURROUND_7_1: 2, AudioLevel.ATMOS: 3}
RESOLUTION = {"4k": VideoLevel.UHD, "2160": VideoLevel.UHD, "1080": VideoLevel.FULL_HD, "720": VideoLevel.HD}
PQ_TRANSFER = "smpte2084"
ACCESSIBILITY = (Badge.SDH, Badge.CC, Badge.AD)
CAPTION_CODECS = frozenset({"eia_608", "eia_708"})
SDH_TITLE = re.compile(r"\bsdh\b")
CC_TITLE = re.compile(r"\b(cc|closed captions?)\b")
AD_TITLE = re.compile(r"\b(ad|audio description|described|descriptive)\b")


@dataclass(frozen=True)
class QualityMinimums:
    video: VideoLevel = VideoLevel.UHD
    hdr: HdrLevel = HdrLevel.HDR10
    audio: AudioLevel = AudioLevel.ATMOS


@dataclass(frozen=True)
class MediaQuality:
    video: VideoLevel
    hdr: HdrLevel
    audio: AudioLevel
    dts_x: bool


def _streams(media: Mapping[str, Any], stream_type: int) -> list[Mapping[str, Any]]:
    return [
        s for part in media.get("Part") or [] for s in part.get("Stream") or [] if s.get("streamType") == stream_type
    ]


def _text(*values: Any) -> str:
    return " ".join(str(v) for v in values if v).lower()


def analyse(media: Mapping[str, Any]) -> MediaQuality:
    video_streams = _streams(media, 1)
    audio_streams = _streams(media, 2)

    video = RESOLUTION.get(str(media.get("videoResolution", "")).lower(), VideoLevel.OFF)

    hdr = HdrLevel.OFF
    for s in video_streams:
        title = _text(s.get("displayTitle"), s.get("extendedDisplayTitle"))
        if s.get("DOVIPresent") or "dolby vision" in title:
            level = HdrLevel.DOLBY_VISION
        elif "hdr10+" in title:
            level = HdrLevel.HDR10_PLUS
        elif str(s.get("colorTrc", "")).lower() == PQ_TRANSFER or "hdr10" in title:
            level = HdrLevel.HDR10
        else:
            continue
        hdr = max(hdr, level, key=HDR_RANK.__getitem__)

    audio = AudioLevel.OFF
    dts_x = False
    for s in audio_streams:
        title = _text(s.get("profile"), s.get("displayTitle"), s.get("extendedDisplayTitle"))
        channels = int(s.get("channels") or 0)
        if "atmos" in title:
            found = AudioLevel.ATMOS
        elif "dts:x" in title or "dts-x" in title:
            found, dts_x = AudioLevel.ATMOS, True
        elif channels >= 8:
            found = AudioLevel.SURROUND_7_1
        elif channels >= 6:
            found = AudioLevel.SURROUND_5_1
        else:
            continue
        audio = max(audio, found, key=AUDIO_RANK.__getitem__)
    if "atmos" in _text(media.get("audioProfile")):
        audio, dts_x = AudioLevel.ATMOS, False
    return MediaQuality(video, hdr, audio, dts_x and audio == AudioLevel.ATMOS)


def best(media_list: Sequence[Mapping[str, Any]]) -> MediaQuality:
    qualities = [analyse(m) for m in media_list] or [MediaQuality(VideoLevel.OFF, HdrLevel.OFF, AudioLevel.OFF, False)]
    return max(qualities, key=lambda q: (VIDEO_RANK[q.video], HDR_RANK[q.hdr], AUDIO_RANK[q.audio]))


def badges(quality: MediaQuality, minimums: QualityMinimums) -> list[Badge]:
    out: list[Badge] = []
    if minimums.video != VideoLevel.OFF and VIDEO_RANK[quality.video] >= VIDEO_RANK[minimums.video] > 0:
        out.append(Badge.UHD if quality.video == VideoLevel.UHD else Badge.HD)
    if minimums.hdr != HdrLevel.OFF and HDR_RANK[quality.hdr] >= HDR_RANK[minimums.hdr] > 0:
        out.append(Badge(quality.hdr.value))
    if minimums.audio != AudioLevel.OFF and AUDIO_RANK[quality.audio] >= AUDIO_RANK[minimums.audio] > 0:
        if quality.audio == AudioLevel.ATMOS:
            out.append(Badge.DTS_X if quality.dts_x else Badge.DOLBY_ATMOS)
        else:
            out.append(Badge(quality.audio.value))
    return out


def accessibility(media_list: Sequence[Mapping[str, Any]], wanted: frozenset[Badge]) -> list[Badge]:
    """SDH, CC and AD from Plex's stream flags, or from track titles when a file sets no flag."""
    found: set[Badge] = set()
    for media in media_list:
        for s in _streams(media, 3):
            title = _text(s.get("title"), s.get("displayTitle"), s.get("extendedDisplayTitle"))
            if s.get("hearingImpaired") or SDH_TITLE.search(title):
                found.add(Badge.SDH)
            if str(s.get("codec", "")).lower() in CAPTION_CODECS or CC_TITLE.search(title):
                found.add(Badge.CC)
        for s in _streams(media, 2):
            title = _text(s.get("title"), s.get("displayTitle"), s.get("extendedDisplayTitle"))
            if s.get("visualImpaired") or AD_TITLE.search(title):
                found.add(Badge.AD)
    return [badge for badge in ACCESSIBILITY if badge in found and badge in wanted]
