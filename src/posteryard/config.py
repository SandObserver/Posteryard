import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from pathlib import Path

from posteryard import notify
from posteryard.quality import ACCESSIBILITY, AudioLevel, Badge, HdrLevel, QualityMinimums, VideoLevel

TRUE = frozenset({"1", "true", "yes", "on"})
FALSE = frozenset({"0", "false", "no", "off"})
CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    pass


class EpisodeMode(StrEnum):
    PLAIN = "plain"
    TITLED = "titled"
    OFF = "off"


@dataclass(frozen=True)
class Config:
    tmdb_api_key: str
    plex_url: str
    plex_token: str
    jellyfin_url: str
    jellyfin_api_key: str
    maintainerr_url: str
    regions: tuple[str, ...]
    quality: QualityMinimums
    status_labels: bool
    accessibility: frozenset[Badge]
    episodes: EpisodeMode
    logo_languages: tuple[str, ...]
    prefer_wordmark: bool
    collection_posters: bool
    service_collections: bool
    data_dir: Path
    libraries: tuple[str, ...]
    dry_run: bool
    only_rating_keys: frozenset[str]
    webhook_secret: str
    sweep_minutes: int
    daily_at: time
    ntfy_url: str
    ntfy_topic: str
    ntfy_token: str
    notify_urls: tuple[str, ...]
    listen_port: int
    heartbeat_url: str

    @property
    def preview_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "recent"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.db"


def _choice[E: StrEnum](env: Mapping[str, str], name: str, default: str, levels: type[E]) -> E:
    raw = env.get(name, "").strip().lower() or default
    try:
        return levels(raw)
    except ValueError:
        allowed = ", ".join(level.value for level in levels)
        raise ConfigError(f"{name} must be one of: {allowed}") from None


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw not in TRUE | FALSE:
        raise ConfigError(f"{name} must be true or false")
    return raw in TRUE


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, "").strip() or str(default)
    if not (raw.isascii() and raw.isdigit()) or not low <= int(raw) <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}")
    return int(raw)


def _list(env: Mapping[str, str], name: str, default: str = "") -> tuple[str, ...]:
    return tuple(part.strip() for part in env.get(name, default).split(",") if part.strip())


def _accessibility(env: Mapping[str, str]) -> frozenset[Badge]:
    allowed = {badge.value: badge for badge in ACCESSIBILITY}
    names = [name.lower() for name in _list(env, "QUALITY_ACCESSIBILITY")]
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise ConfigError(f"QUALITY_ACCESSIBILITY takes sdh, cc and ad, not {', '.join(unknown)}")
    return frozenset(allowed[name] for name in names)


def _languages(env: Mapping[str, str]) -> tuple[str, ...]:
    languages = tuple(code.lower() for code in _list(env, "LOGO_LANGUAGES", "en"))
    bad = [code for code in languages if not (len(code) == 2 and code.isascii() and code.isalpha())]
    if bad or not languages:
        raise ConfigError("LOGO_LANGUAGES takes two-letter language codes such as fr,en")
    return languages


def _clock(env: Mapping[str, str], name: str, default: str) -> time:
    match = CLOCK.match(env.get(name, "").strip() or default)
    if not match:
        raise ConfigError(f"{name} must be a 24-hour time such as 04:15")
    return time(int(match.group(1)), int(match.group(2)))


def load(env: Mapping[str, str] = os.environ) -> Config:
    tmdb_api_key = env.get("TMDB_API_KEY", "").strip()
    if not tmdb_api_key:
        raise ConfigError("TMDB_API_KEY is required")
    notify_urls = tuple(notify.split_urls(env.get("NOTIFY_URLS", "")))
    bad = notify.invalid_urls(notify_urls)
    if bad:
        raise ConfigError(f"NOTIFY_URLS: address {', '.join(map(str, bad))} is not one Apprise supports")
    heartbeat_url = env.get("HEARTBEAT_URL", "").strip()
    if heartbeat_url and not heartbeat_url.startswith(("http://", "https://")):
        raise ConfigError("HEARTBEAT_URL must start with http:// or https://")
    library_setting = "LIBRARIES" if env.get("LIBRARIES", "").strip() else "PLEX_LIBRARIES"
    libraries = _list(env, library_setting, "Movies,TV Shows")
    if not libraries:
        raise ConfigError(f"{library_setting} needs at least one library name")
    return Config(
        tmdb_api_key=tmdb_api_key,
        plex_url=env.get("PLEX_URL", "").strip().rstrip("/"),
        plex_token=env.get("PLEX_TOKEN", "").strip(),
        jellyfin_url=env.get("JELLYFIN_URL", "").strip().rstrip("/"),
        jellyfin_api_key=env.get("JELLYFIN_API_KEY", "").strip(),
        maintainerr_url=env.get("MAINTAINERR_URL", "").strip().rstrip("/"),
        regions=tuple(r.upper() for r in _list(env, "STREAMING_REGIONS", "US")),
        quality=QualityMinimums(
            video=_choice(env, "QUALITY_MIN_VIDEO", "2160", VideoLevel),
            hdr=_choice(env, "QUALITY_MIN_HDR", "hdr10", HdrLevel),
            audio=_choice(env, "QUALITY_MIN_AUDIO", "atmos", AudioLevel),
        ),
        status_labels=_bool(env, "STATUS_LABELS", default=True),
        accessibility=_accessibility(env),
        episodes=_choice(env, "EPISODE_THUMBNAILS", "plain", EpisodeMode),
        logo_languages=_languages(env),
        prefer_wordmark=_bool(env, "PREFER_WORDMARK", default=True),
        collection_posters=_bool(env, "COLLECTION_POSTERS", default=False),
        service_collections=_bool(env, "SERVICE_COLLECTIONS", default=False),
        data_dir=Path(env.get("DATA_DIR", "data")),
        libraries=libraries,
        dry_run=_bool(env, "DRY_RUN", default=True),
        only_rating_keys=frozenset(_list(env, "ONLY_RATING_KEYS")),
        webhook_secret=env.get("WEBHOOK_SECRET", "").strip(),
        sweep_minutes=_int(env, "SWEEP_MINUTES", 15, 1, 1440),
        daily_at=_clock(env, "DAILY_AT", "04:15"),
        ntfy_url=env.get("NTFY_URL", "").strip().rstrip("/"),
        ntfy_topic=env.get("NTFY_TOPIC", "").strip(),
        ntfy_token=env.get("NTFY_TOKEN", "").strip(),
        notify_urls=notify_urls,
        listen_port=_int(env, "LISTEN_PORT", 8000, 1, 65535),
        heartbeat_url=heartbeat_url,
    )


def require_server(cfg: Config) -> None:
    plex, jellyfin = bool(cfg.plex_url or cfg.plex_token), bool(cfg.jellyfin_url or cfg.jellyfin_api_key)
    if plex and jellyfin:
        raise ConfigError("Set PLEX_URL and PLEX_TOKEN, or JELLYFIN_URL and JELLYFIN_API_KEY, not both")
    if jellyfin and not (cfg.jellyfin_url and cfg.jellyfin_api_key):
        raise ConfigError("JELLYFIN_URL and JELLYFIN_API_KEY are both required")
    if not jellyfin and not (cfg.plex_url and cfg.plex_token):
        raise ConfigError("PLEX_URL and PLEX_TOKEN are required, or JELLYFIN_URL and JELLYFIN_API_KEY for Jellyfin")


def require_service(cfg: Config) -> None:
    require_server(cfg)
    if not cfg.webhook_secret:
        raise ConfigError("WEBHOOK_SECRET is required to run the service")
