"""What was rendered or uploaded where, so the service is idempotent and leaves manual picks alone."""

import json
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from posteryard.overrides import Override

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    rating_key  TEXT NOT NULL,
    target      TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    image_key   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    failures    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (rating_key, target)
);
CREATE TABLE IF NOT EXISTS overrides (
    rating_key TEXT PRIMARY KEY,
    custom     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    skip       TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS choices (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
COLUMNS = "rating_key, target, title, fingerprint, image_key, status, failures, updated_at, last_error"
RETRY_FIRST = 900
RETRY_MAX = 12 * 3600


class Status(StrEnum):
    UPLOADED = "uploaded"
    PREVIEW = "preview"
    MANUAL = "manual"
    FAILED = "failed"


@dataclass(frozen=True)
class Record:
    rating_key: str
    target: str
    title: str
    fingerprint: str
    image_key: str
    status: str
    failures: int
    updated_at: int
    last_error: str = ""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def get(self, rating_key: str, target: str) -> Record | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {COLUMNS} FROM images WHERE rating_key=? AND target=?",
                (rating_key, target),
            ).fetchone()
        return Record(*row) if row else None

    def uploaded(self, rating_key: str, target: str, title: str, fingerprint: str, image_key: str) -> None:
        self._upsert(rating_key, target, title=title, fingerprint=fingerprint, image_key=image_key,
                     status=Status.UPLOADED, failures=0, last_error="")  # fmt: skip

    def previewed(self, rating_key: str, target: str, title: str, fingerprint: str) -> None:
        self._upsert(rating_key, target, title=title, fingerprint=fingerprint, status=Status.PREVIEW,
                     failures=0, last_error="")  # fmt: skip

    def manual(self, rating_key: str, target: str, title: str) -> None:
        self._upsert(rating_key, target, title=title, status=Status.MANUAL)

    def failed(self, rating_key: str, target: str, title: str, error: str) -> int:
        record = self.get(rating_key, target)
        failures = (record.failures if record else 0) + 1
        self._upsert(rating_key, target, title=title, status=Status.FAILED, failures=failures, last_error=error[:500])
        return failures

    def forget_target(self, rating_key: str, target: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM images WHERE rating_key=? AND target=?", (rating_key, target))

    def forget(self, rating_key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM images WHERE rating_key=?", (rating_key,))

    def retry_due(self, now: float | None = None) -> list[str]:
        """Failed items whose backoff has passed: 15 minutes, doubling, at most 12 hours."""
        now = time.time() if now is None else now
        with self._lock:
            rows = self._db.execute(
                "SELECT rating_key, failures, updated_at FROM images WHERE status=?", (Status.FAILED,)
            ).fetchall()
        due = {key for key, fails, at in rows if now - at >= min(RETRY_FIRST * 2 ** (fails - 1), RETRY_MAX)}
        return sorted(due)

    def keys(self) -> set[str]:
        with self._lock:
            return {row[0] for row in self._db.execute("SELECT DISTINCT rating_key FROM images")}

    def recent(self, limit: int) -> list[Record]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {COLUMNS} FROM images WHERE status != ? ORDER BY updated_at DESC LIMIT ?",
                (Status.FAILED, limit),
            ).fetchall()
        return [Record(*row) for row in rows]

    def failures(self) -> list[Record]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {COLUMNS} FROM images WHERE status = ? ORDER BY updated_at DESC", (Status.FAILED,)
            ).fetchall()
        return [Record(*row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._db.execute("SELECT status, COUNT(*) FROM images GROUP BY status").fetchall())

    def override(self, rating_key: str) -> Override | None:
        with self._lock:
            row = self._db.execute(
                "SELECT custom, source, skip FROM overrides WHERE rating_key=?", (rating_key,)
            ).fetchone()
        if not row:
            return None
        return Override(custom=row[0] or None, source=row[1], skip=frozenset(json.loads(row[2])))

    def set_custom(self, rating_key: str, path: str, source: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO overrides(rating_key, custom, source, skip) VALUES(?, ?, ?, '[]') "
                "ON CONFLICT(rating_key) DO UPDATE SET custom=excluded.custom, source=excluded.source, skip='[]'",
                (rating_key, path, source),
            )

    def add_skip(self, rating_key: str, art: str) -> None:
        current = self.override(rating_key)
        skip = sorted((current.skip if current else frozenset()) | {art})
        with self._lock:
            self._db.execute(
                "INSERT INTO overrides(rating_key, custom, source, skip) VALUES(?, '', '', ?) "
                "ON CONFLICT(rating_key) DO UPDATE SET custom='', source='', skip=excluded.skip",
                (rating_key, json.dumps(skip)),
            )

    def reset_override(self, rating_key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM overrides WHERE rating_key=?", (rating_key,))

    def get_choice(self, key: str) -> Mapping[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM choices WHERE key=?", (key,)).fetchone()
        value: Mapping[str, Any] | None = json.loads(row[0]) if row else None
        return value

    def put_choice(self, key: str, value: Mapping[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO choices(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _upsert(self, rating_key: str, target: str, **fields: object) -> None:
        fields["updated_at"] = int(time.time())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{c}=excluded.{c}" for c in fields)
        with self._lock:
            self._db.execute(
                f"INSERT INTO images(rating_key, target, {cols}) VALUES(?, ?, {marks}) "
                f"ON CONFLICT(rating_key, target) DO UPDATE SET {updates}",
                (rating_key, target, *fields.values()),
            )
