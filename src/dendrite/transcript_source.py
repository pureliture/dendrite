"""Shared read-only SQLite access for transcript source adapters.

The store is opened with ``mode=ro`` and ``PRAGMA query_only=ON``.  Unlike
``immutable=1``, this keeps committed WAL frames visible, so a session that was
written just before an unclean shutdown is still readable.  No write or
checkpoint statement is ever issued.  Busy/locked stores are retried briefly and
then fail closed; unreadable, missing, or corrupt stores fail closed as well.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_BUSY_RETRIES = 5
_BUSY_BACKOFF_SECONDS = 0.01
_BUSY_MARKERS = ("locked", "busy")


def readonly_wal_uri(path: Path | str) -> str:
    """Read-only URI that does not disable WAL visibility."""
    return Path(path).resolve().as_uri() + "?mode=ro"


def readonly_wal_connection(path: Path | str) -> sqlite3.Connection:
    """Open *path* read-only with WAL visibility and no write capability.

    Raises ``ValueError("source_unreadable")`` for missing/corrupt files, and
    after bounded retries for busy/locked files.  The caller owns the returned
    connection and must close it.
    """
    uri = readonly_wal_uri(path)
    for attempt in range(_BUSY_RETRIES + 1):
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=0.2)
            connection.execute("PRAGMA query_only = ON")
            # Touch the schema now so corruption is reported here, not later.
            connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            return connection
        except sqlite3.OperationalError as exc:
            if connection is not None:
                connection.close()
            message = str(exc).lower()
            if any(marker in message for marker in _BUSY_MARKERS) and attempt < _BUSY_RETRIES:
                time.sleep(_BUSY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise ValueError("source_unreadable") from exc
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise ValueError("source_unreadable") from exc
    raise ValueError("source_unreadable")  # pragma: no cover - loop always returns/raises
