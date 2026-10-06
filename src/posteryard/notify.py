import json
import logging
import re
import threading
import time
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

import apprise
from apprise.attachment.memory import AttachMemory

log = logging.getLogger(__name__)
QUIET_SECONDS = 6 * 3600
NEW_LISTED = 10
SEPARATOR = re.compile(r"[,\s]+")
OPEN_ALERTS = "open_alerts"


class Event(StrEnum):
    PROBLEMS = "problems"
    NEW = "new"
    SUMMARY = "summary"


class State(Protocol):
    def meta(self, key: str, default: str = "") -> str: ...
    def set_meta(self, key: str, value: str) -> None: ...


def split_urls(text: str) -> list[str]:
    return [url for url in SEPARATOR.split(text.strip()) if url]


def invalid_urls(urls: Sequence[str]) -> list[int]:
    """The 1-based positions of the addresses Apprise cannot use. Addresses hold secrets, so they are not echoed."""
    return [n for n, url in enumerate(urls, 1) if not apprise.Apprise().add(url)]


class Notifier:
    def __init__(
        self,
        apprise_urls: Sequence[str] = (),
        events: frozenset[Event] = frozenset({Event.PROBLEMS}),
        state: State | None = None,
    ) -> None:
        self._apprise = apprise.Apprise()
        for address in apprise_urls:
            self._apprise.add(address)
        self.events = events
        self._state = state
        self._lock = threading.Lock()
        self._open: dict[str, float] = json.loads(state.meta(OPEN_ALERTS, "{}")) if state else {}

    @property
    def configured(self) -> bool:
        return len(self._apprise) > 0

    def alert(self, cause: str, message: str) -> None:
        """A problem. Each cause is sent at most once per quiet period, also across restarts."""
        with self._lock:
            now = time.time()
            if now - self._open.get(cause, float("-inf")) < QUIET_SECONDS:
                return
            self._open[cause] = now
            self._save()
        if Event.PROBLEMS in self.events:
            self.send(cause, message)

    def resolve(self, cause: str, message: str) -> None:
        """The end of a problem. Sent only when an alert for the cause went out before."""
        with self._lock:
            if self._open.pop(cause, None) is None:
                return
            self._save()
        log.info("problem resolved", extra={"cause": cause})
        if Event.PROBLEMS in self.events:
            self.send(cause, message, apprise.NotifyType.SUCCESS)

    def new_posters(self, names: Sequence[str], poster: bytes | None = None) -> None:
        if Event.NEW not in self.events or not names:
            return
        listed = list(names[:NEW_LISTED])
        if len(names) > NEW_LISTED:
            listed.append(f"and {len(names) - NEW_LISTED} more")
        subject = "new poster" if len(names) == 1 else f"{len(names)} new posters"
        attach = None
        if poster and len(names) == 1:
            attach = AttachMemory(poster, "poster.jpg", "image/jpeg")  # type: ignore[no-untyped-call]
        self.send(subject, "\n".join(listed), apprise.NotifyType.INFO, attach)

    def summary(self, message: str, *, failed: bool) -> None:
        if Event.SUMMARY in self.events:
            kind = apprise.NotifyType.WARNING if failed else apprise.NotifyType.SUCCESS
            self.send("daily check", message, kind)

    def send(
        self,
        subject: str,
        message: str,
        kind: apprise.NotifyType = apprise.NotifyType.WARNING,
        attach: AttachMemory | None = None,
    ) -> bool:
        if not self.configured:
            return False
        if self._apprise.notify(body=message, title=f"Posteryard: {subject}", notify_type=kind, attach=attach):
            return True
        log.warning("notification not delivered to every NOTIFY_URLS service", extra={"subject": subject})
        return False

    def _save(self) -> None:
        if self._state is not None:
            self._state.set_meta(OPEN_ALERTS, json.dumps(self._open, sort_keys=True))
