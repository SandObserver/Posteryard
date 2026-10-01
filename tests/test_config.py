import pytest

from posteryard.config import ConfigError, load
from posteryard.quality import AudioLevel, HdrLevel, VideoLevel


def test_defaults() -> None:
    cfg = load({"TMDB_API_KEY": "example"})
    assert cfg.regions == ("CA", "US")
    assert (cfg.quality.video, cfg.quality.hdr, cfg.quality.audio) == (VideoLevel.UHD, HdrLevel.HDR10, AudioLevel.ATMOS)


def test_quality_minimums_are_validated() -> None:
    cfg = load({"TMDB_API_KEY": "example", "QUALITY_MIN_VIDEO": "1080", "QUALITY_MIN_AUDIO": "OFF"})
    assert cfg.quality.video == VideoLevel.FULL_HD
    assert cfg.quality.audio == AudioLevel.OFF
    with pytest.raises(ConfigError, match="QUALITY_MIN_HDR"):
        load({"TMDB_API_KEY": "example", "QUALITY_MIN_HDR": "hdr12"})


def test_tmdb_key_is_required() -> None:
    with pytest.raises(ConfigError):
        load({})
