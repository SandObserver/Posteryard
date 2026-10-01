"""ntfy alerts, at most one per subject per six hours."""

import logging
import time

from posteryard import http

log = logging.getLogger(__name__)
QUIET_SECONDS = 6 * 3600


class Notifier:
    def __init__(self, url: str, topic: str, token: str) -> None:
        self.url, self.topic, self.token = url, topic, token
        self._sent: dict[str, float] = {}

    def alert(self, subject: str, message: str) -> None:
        now = time.monotonic()
        if subject in self._sent and now - self._sent[subject] < QUIET_SECONDS:
            return
        self._sent[subject] = now
        if not (self.url and self.topic):
            log.warning("alert not sent, ntfy is not set up: %s: %s", subject, message)
            return
        headers = {"Title": f"Posteryard: {subject}", "Tags": "frame_with_picture"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            http.request("POST", f"{self.url}/{self.topic}", data=message.encode(), headers=headers, retries=2)
        except http.RequestError as exc:
            log.warning("could not send an ntfy alert: %s", exc)
