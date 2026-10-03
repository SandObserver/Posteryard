"""Alerts, at most one per subject per six hours, to ntfy and to any service Apprise supports."""

import logging
import re
import time
from collections.abc import Sequence

import apprise

from posteryard import http

log = logging.getLogger(__name__)
QUIET_SECONDS = 6 * 3600
SEPARATOR = re.compile(r"[,\s]+")


def split_urls(text: str) -> list[str]:
    return [url for url in SEPARATOR.split(text.strip()) if url]


def invalid_urls(urls: Sequence[str]) -> list[int]:
    """The 1-based positions of the addresses Apprise cannot use. Addresses hold secrets, so they are not echoed."""
    return [n for n, url in enumerate(urls, 1) if not apprise.Apprise().add(url)]


class Notifier:
    def __init__(self, url: str, topic: str, token: str, apprise_urls: Sequence[str] = ()) -> None:
        self.url, self.topic, self.token = url, topic, token
        self._apprise = apprise.Apprise()
        for address in apprise_urls:
            self._apprise.add(address)
        self._sent: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.url and self.topic) or len(self._apprise) > 0

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
        """Send now, without the quiet period. True when every service accepted it."""
        ok = True
        if self.url and self.topic:
            headers = {"Title": f"Posteryard: {subject}", "Tags": "frame_with_picture"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            try:
                http.request("POST", f"{self.url}/{self.topic}", data=message.encode(), headers=headers, retries=2)
            except http.RequestError as exc:
                log.warning("could not send an ntfy alert: %s", exc)
                ok = False
        if len(self._apprise) and not self._apprise.notify(
            body=message, title=f"Posteryard: {subject}", notify_type=apprise.NotifyType.WARNING
        ):
            log.warning("could not send an alert to every NOTIFY_URLS service")
            ok = False
        return ok
