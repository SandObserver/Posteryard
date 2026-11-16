from dataclasses import dataclass, field

CUSTOM_LABEL = "posteryard-custom"
NEXT_LABEL = "posteryard-next"
IGNORE_LABEL = "posteryard-ignore"
FROM_LABEL = "label"
FROM_COMMAND = "command"
# Older releases stored art taken through the label with this source. It must still count as from the label.
OLD_FROM_LABEL = "plex"


@dataclass(frozen=True)
class Override:
    custom: str | None = None
    source: str = ""
    skip: frozenset[str] = field(default_factory=frozenset)

    @property
    def from_label(self) -> bool:
        return self.source in (FROM_LABEL, OLD_FROM_LABEL)

    @property
    def origin(self) -> str:
        return f"the {CUSTOM_LABEL} label" if self.from_label else "`art set`"
