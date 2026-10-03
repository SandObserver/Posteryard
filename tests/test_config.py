from datetime import time

import pytest

from posteryard.config import ConfigError, load, require_service
from posteryard.quality import AudioLevel, HdrLevel, VideoLevel


def test_defaults() -> None:
    cfg = load({"TMDB_API_KEY": "example"})
    assert cfg.regions == ("US",)
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


def test_service_settings() -> None:
    cfg = load({"TMDB_API_KEY": "example", "DRY_RUN": "false", "ONLY_RATING_KEYS": "1, 2", "DAILY_AT": "3:05"})
    assert cfg.dry_run is False
    assert cfg.only_rating_keys == frozenset({"1", "2"})
    assert cfg.daily_at == time(3, 5)
    assert load({"TMDB_API_KEY": "example"}).dry_run is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DRY_RUN", "maybe"),
        ("DAILY_AT", "25:00"),
        ("SWEEP_MINUTES", "0"),
        ("SWEEP_MINUTES", "²"),
        ("LISTEN_PORT", "٨٠"),
    ],
)
def test_bad_service_settings(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load({"TMDB_API_KEY": "example", name: value})


def test_an_empty_library_list_is_rejected() -> None:
    with pytest.raises(ConfigError, match="PLEX_LIBRARIES"):
        load({"TMDB_API_KEY": "example", "PLEX_LIBRARIES": " , "})


def test_serve_needs_plex_and_a_webhook_secret() -> None:
    with pytest.raises(ConfigError, match="PLEX_URL, PLEX_TOKEN, WEBHOOK_SECRET"):
        require_service(load({"TMDB_API_KEY": "example"}))
