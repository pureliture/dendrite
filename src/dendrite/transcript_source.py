"""Provider transcript source adapters.

One interface, one adapter per provider storage shape. The thin shipper
(`transcript_drain`) reads a capture request's local source through the matching
adapter and gets back a single redacted transcript text, then packs it into the
same `conversation_chunk` document for every provider.

- Text-file providers (codex/claude/gemini/antigravity) store one jsonl file per
  session: the adapter reads and redacts the file as opaque text.
- Hermes stores all sessions in one local SQLite store (`~/.hermes/state.db`): the
  adapter opens it read-only/immutable (no locks, no WAL checkpoint, never writes),
  selects the requested session's messages, and assembles a redacted transcript.

dendrite still does NOT do session-memory build/promote, GC, or RAGFlow work — it
only produces a redacted transcript document, exactly as it already did for jsonl.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .redaction import redact_public_ingress_text

MAX_TRANSCRIPT_BODY_CHARS = 180_000
# Schema per Hermes session-storage docs; tolerant to column presence. Verify
# against a live Hermes install before marking the contract source-verified.
HERMES_MESSAGES_TABLE = "messages"


class TranscriptSourceAdapter:
    def read_redacted_transcript(self, request: dict) -> str:
        """Return the redacted transcript text for the request's local source.

        Raises ValueError with one of the source_* error classes
        ('source_unproven' | 'source_policy_blocked' | 'source_unreadable') so the
        drain can classify and quarantine without leaking anything.
        """
        raise NotImplementedError


class JsonlSourceAdapter(TranscriptSourceAdapter):
    """codex/claude/gemini/antigravity: one jsonl file per session (opaque text)."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = _source_path(locator.get("runtime_handle"))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError("source_unreadable") from exc
        return _redact_and_bound(text)


class HermesSqliteSourceAdapter(TranscriptSourceAdapter):
    """hermes: one SQLite store for all sessions; read-only, never written."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = _source_path(locator.get("runtime_handle"))
        session_id = str(request.get("session_id") or "")
        text = _read_hermes_session_text(path, session_id)
        return _redact_and_bound(text)


_DEFAULT_ADAPTER = JsonlSourceAdapter()
_ADAPTERS: dict[str, TranscriptSourceAdapter] = {"hermes": HermesSqliteSourceAdapter()}


def adapter_for(provider: str) -> TranscriptSourceAdapter:
    return _ADAPTERS.get(provider, _DEFAULT_ADAPTER)


def _source_path(value) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("source_unproven")
    path = Path(value)
    if path.is_symlink():
        raise ValueError("source_policy_blocked")
    if not path.exists() or not path.is_file():
        raise ValueError("source_unreadable")
    return path


def _redact_and_bound(text: str) -> str:
    redacted = redact_public_ingress_text(text)
    if len(redacted) > MAX_TRANSCRIPT_BODY_CHARS:
        return redacted[: MAX_TRANSCRIPT_BODY_CHARS - len("\n[truncated]\n")] + "\n[truncated]\n"
    return redacted


def _read_hermes_session_text(path: Path, session_id: str) -> str:
    """Read one Hermes session's messages from the SQLite store, read-only.

    Opens with mode=ro&immutable=1 so it never locks the store or triggers a WAL
    checkpoint and never writes. Returns assembled "role: content" lines. The store
    is selected by session_id when that column exists; otherwise all messages are
    read (a single-session store).
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as exc:
        raise ValueError("source_unreadable") from exc
    try:
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({HERMES_MESSAGES_TABLE})")}
        if "content" not in columns:
            raise ValueError("source_unreadable")
        order_by = "timestamp" if "timestamp" in columns else "rowid"
        where, params = "", ()
        if session_id and "session_id" in columns:
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
