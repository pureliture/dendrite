"""Hermes SQLite source adapter (read-only, profile-local store)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .base import TranscriptSourceAdapter
from .common import source_path
from ..redaction import redact_public_ingress_text

HERMES_MESSAGES_TABLE = "messages"


def hermes_ro_uri(path: Path) -> str:
    return f"file:{path}?mode=ro&immutable=1"


class HermesSqliteSourceAdapter(TranscriptSourceAdapter):
    """hermes: one profile-local SQLite store; read-only, never written."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = source_path(locator.get("runtime_handle"))
        session_id = str(request.get("session_id") or "")
        text = read_hermes_session_text(path, session_id)
        return redact_public_ingress_text(text)


def enumerate_hermes_sessions(db_path) -> list[str]:
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        return []
    try:
        conn = sqlite3.connect(hermes_ro_uri(path), uri=True)
    except sqlite3.Error:
        return []
    try:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({HERMES_MESSAGES_TABLE})")}
        if not columns:
            return []
        if "session_id" not in columns:
            return [""]
        rows = conn.execute(
            f"SELECT DISTINCT session_id FROM {HERMES_MESSAGES_TABLE} "
            "WHERE session_id IS NOT NULL ORDER BY session_id"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def read_hermes_session_text(path: Path, session_id: str) -> str:
    try:
        conn = sqlite3.connect(hermes_ro_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise ValueError("source_unreadable") from exc
    try:
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({HERMES_MESSAGES_TABLE})")}
        if "content" not in columns:
            raise ValueError("source_unreadable")
        order_by = "timestamp" if "timestamp" in columns else "rowid"
        where, params = "", ()
        if "session_id" in columns:
            if not session_id:
                raise ValueError("source_unreadable")
            where, params = "WHERE session_id = ?", (session_id,)
        select = "role, content" if "role" in columns else "content"
        query = f"SELECT {select} FROM {HERMES_MESSAGES_TABLE} {where} ORDER BY {order_by}"
        rows = conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("source_unreadable") from exc
    finally:
        conn.close()
    keys = set(rows[0].keys()) if rows else set()
    lines = []
    for row in rows:
        content = row["content"] if "content" in keys else ""
        content = "" if content is None else str(content)
        role = str(row["role"]) if "role" in keys and row["role"] is not None else ""
        lines.append(f"{role}: {content}" if role else content)
    return "\n".join(lines)
