import logging
import re
import time
from collections.abc import Sequence

import apprise

log = logging.getLogger(__name__)
QUIET_SECONDS = 6 * 3600
SEPARATOR = re.compile(r"[,\s]+")


def split_urls(text: str) -> list[str]:
    return [url for url in SEPARATOR.split(text.strip()) if url]


def invalid_urls(urls: Sequence[str]) -> list[int]:
    """The 1-based positions of the addresses Apprise cannot use. Addresses hold secrets, so they are not echoed."""
    return [n for n, url in enumerate(urls, 1) if not apprise.Apprise().add(url)]


class Notifier:
    def __init__(self, apprise_urls: Sequence[str] = ()) -> None:
        self._apprise = apprise.Apprise()
        for address in apprise_urls:
            self._apprise.add(address)
        self._sent: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return len(self._apprise) > 0

    def alert(self, subject: str, message: str) -> None:
        now = time.monotonic()
        if subject in self._sent and now - self._sent[subject] < QUIET_SECONDS:
            return
        self._sent[subject] = now
        if not self.configured:
            log.warning("alert not sent, no notification service is set up: %s: %s", subject, message)
            return
        self.send(subject, message)

    def send(self, subject: str, message: str) -> bool:
        if self._apprise.notify(body=message, title=f"Posteryard: {subject}", notify_type=apprise.NotifyType.WARNING):
            return True
        log.warning("could not send an alert to every NOTIFY_URLS service")
        return False
