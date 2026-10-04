from datetime import time
from pathlib import Path

import pytest

from posteryard.config import ConfigError, EpisodeMode, load, require_service
from posteryard.quality import AudioLevel, Badge, HdrLevel, VideoLevel


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


def test_secrets_can_come_from_files(tmp_path: Path) -> None:
    (tmp_path / "tmdb").write_text("from-file\n")
    cfg = load({"TMDB_API_KEY_FILE": str(tmp_path / "tmdb")})
    assert cfg.tmdb_api_key == "from-file"
    with pytest.raises(ConfigError, match="not both"):
        load({"TMDB_API_KEY": "a", "TMDB_API_KEY_FILE": str(tmp_path / "tmdb")})
    with pytest.raises(ConfigError, match="PLEX_TOKEN_FILE could not be read: FileNotFoundError"):
        load({"TMDB_API_KEY": "a", "PLEX_TOKEN_FILE": str(tmp_path / "missing")})


def test_log_level() -> None:
    assert load({"TMDB_API_KEY": "a"}).log_level == "info"
    assert load({"TMDB_API_KEY": "a", "LOG_LEVEL": "DEBUG"}).log_level == "debug"
    with pytest.raises(ConfigError, match="LOG_LEVEL must be one of"):
        load({"TMDB_API_KEY": "a", "LOG_LEVEL": "loud"})


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


def test_serve_needs_one_server_and_a_webhook_secret() -> None:
    with pytest.raises(ConfigError, match="PLEX_URL and PLEX_TOKEN are required"):
        require_service(load({"TMDB_API_KEY": "example"}))
    plex = {"TMDB_API_KEY": "example", "PLEX_URL": "http://plex.example:32400", "PLEX_TOKEN": "example"}
    with pytest.raises(ConfigError, match="WEBHOOK_SECRET"):
        require_service(load(plex))
    jellyfin = {"TMDB_API_KEY": "example", "JELLYFIN_URL": "http://jellyfin.example:8096", "WEBHOOK_SECRET": "x"}
    with pytest.raises(ConfigError, match="both required"):
        require_service(load(jellyfin))
    require_service(load({**jellyfin, "JELLYFIN_API_KEY": "example"}))
    with pytest.raises(ConfigError, match="not both"):
        require_service(load({**plex, **jellyfin, "JELLYFIN_API_KEY": "example"}))


def test_libraries_setting_wins_over_the_plex_name() -> None:
    cfg = load({"TMDB_API_KEY": "example", "LIBRARIES": "Films,Series", "PLEX_LIBRARIES": "Movies"})
    assert cfg.libraries == ("Films", "Series")
    assert load({"TMDB_API_KEY": "example", "PLEX_LIBRARIES": "Movies"}).libraries == ("Movies",)


def test_poster_options() -> None:
    cfg = load({"TMDB_API_KEY": "example"})
    assert cfg.accessibility == frozenset()
    assert cfg.episodes == EpisodeMode.PLAIN
    assert cfg.logo_languages == ("en",)
    assert cfg.prefer_wordmark is True
    assert cfg.status_labels is True
    assert (cfg.collection_posters, cfg.service_collections) == (False, False)
    cfg = load(
        {"TMDB_API_KEY": "example", "QUALITY_ACCESSIBILITY": "SDH, ad", "EPISODE_THUMBNAILS": "off",
         "LOGO_LANGUAGES": "fr,EN", "PREFER_WORDMARK": "false"}
    )  # fmt: skip
    assert cfg.accessibility == frozenset({Badge.SDH, Badge.AD})
    assert cfg.episodes == EpisodeMode.OFF
    assert cfg.logo_languages == ("fr", "en")
    with pytest.raises(ConfigError, match="QUALITY_ACCESSIBILITY"):
        load({"TMDB_API_KEY": "example", "QUALITY_ACCESSIBILITY": "sdh,braille"})
    with pytest.raises(ConfigError, match="LOGO_LANGUAGES"):
        load({"TMDB_API_KEY": "example", "LOGO_LANGUAGES": "french"})
