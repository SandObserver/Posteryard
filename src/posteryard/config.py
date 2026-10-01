"""Settings from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from posteryard.quality import AudioLevel, HdrLevel, QualityMinimums, VideoLevel


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    tmdb_api_key: str
    plex_url: str
    plex_token: str
    maintainerr_url: str
    regions: tuple[str, ...]
    quality: QualityMinimums
    data_dir: Path

    @property
    def preview_dir(self) -> Path:
        return self.data_dir / "previews"


def _choice[E: StrEnum](env: Mapping[str, str], name: str, default: str, levels: type[E]) -> E:
    raw = env.get(name, "").strip().lower() or default
    try:
        return levels(raw)
    except ValueError:
        allowed = ", ".join(level.value for level in levels)
        raise ConfigError(f"{name} must be one of: {allowed}") from None


def load(env: Mapping[str, str] = os.environ) -> Config:
    tmdb_api_key = env.get("TMDB_API_KEY", "").strip()
    if not tmdb_api_key:
        raise ConfigError("TMDB_API_KEY is required")
    return Config(
        tmdb_api_key=tmdb_api_key,
        plex_url=env.get("PLEX_URL", "").strip().rstrip("/"),
        plex_token=env.get("PLEX_TOKEN", "").strip(),
        maintainerr_url=env.get("MAINTAINERR_URL", "").strip().rstrip("/"),
        regions=tuple(r.strip().upper() for r in env.get("STREAMING_REGIONS", "CA,US").split(",") if r.strip()),
        quality=QualityMinimums(
            video=_choice(env, "QUALITY_MIN_VIDEO", "2160", VideoLevel),
            hdr=_choice(env, "QUALITY_MIN_HDR", "hdr10", HdrLevel),
            audio=_choice(env, "QUALITY_MIN_AUDIO", "atmos", AudioLevel),
        ),
        data_dir=Path(env.get("DATA_DIR", "data")),
    )
