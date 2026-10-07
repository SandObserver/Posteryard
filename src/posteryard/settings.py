from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from posteryard import maintainerr
from posteryard.config import Config, EpisodeMode
from posteryard.quality import Badge, QualityMinimums


@dataclass
class Settings:
    minimums: QualityMinimums
    regions: tuple[str, ...]
    action_days: Mapping[str, date]
    today: date
    labels: bool = True
    accessibility: frozenset[Badge] = frozenset()
    episodes: EpisodeMode = EpisodeMode.PLAIN
    logo_languages: tuple[str, ...] = ("en",)
    prefer_wordmark: bool = True
    apple_region: str | None = None

    @classmethod
    def from_config(cls, cfg: Config, action_days: Mapping[str, date], today: date) -> "Settings":
        return cls(
            cfg.quality,
            cfg.regions,
            action_days,
            today,
            labels=cfg.status_labels,
            accessibility=cfg.accessibility,
            episodes=cfg.episodes,
            logo_languages=cfg.logo_languages,
            prefer_wordmark=cfg.prefer_wordmark,
            apple_region=cfg.regions[0] if cfg.apple_art and cfg.regions else None,
        )

    def leaving(self, *rating_keys: str) -> str | None:
        for key in rating_keys:
            days = maintainerr.days_left(self.action_days.get(key), self.today)
            if days is not None:
                return maintainerr.label(days)
        return None
