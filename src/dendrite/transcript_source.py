"""Provider transcript source adapters.

One interface, one adapter per provider storage shape. The thin shipper
(`transcript_drain`) reads a capture request's local source through the matching
adapter and gets back the complete redacted transcript text. The drain splits
it into bounded `conversation_chunk` documents without dropping the tail.

- Text-file providers (codex/claude/gemini/antigravity/grok) store one jsonl file
  per session (Grok: ``updates.jsonl``): the adapter reads and redacts the file as
  opaque text.
- Hermes stores sessions in profile-local SQLite stores (`~/.hermes/state.db` or
  `~/.hermes/profiles/<profile>/state.db`): the adapter opens the selected store
  read-only/immutable (no locks, no WAL checkpoint, never writes), selects the
  requested session's messages, and assembles a redacted transcript.
- OpenCode stores sessions in a single SQLite store
  (`~/.local/share/opencode/opencode.db`, tables ``session_v2`` +
  ``session_message``): the adapter opens it read-only/immutable and assembles
  the requested session's user/assistant text the same way.

dendrite still does NOT do session-memory build/promote, GC, or backend work — it
only produces a redacted transcript document, exactly as it already did for jsonl.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .redaction import redact_public_ingress_text

# Per-document serialized body budget; never a limit on the source transcript.
MAX_TRANSCRIPT_BODY_CHARS = 180_000
# Schema per Hermes session-storage docs; tolerant to column presence. Verify
# against a live Hermes install before marking the contract source-verified.
HERMES_MESSAGES_TABLE = "messages"
# OpenCode session store: single SQLite db with session_v2 + session_message.
# Verified against opencode v2.1.2 local install (session_message: id/session_id/
# type/seq/time_created/data[JSON]). Drain reads only user/assistant/synthetic
# text; system/idle rows are skipped (catalog/outcome noise, not conversation).
OPENCODE_MESSAGES_TABLE = "session_message"
OPENCODE_SESSIONS_TABLE = "session_v2"


def _hermes_ro_uri(path: Path) -> str:
    """SQLite URI that opens the Hermes store read-only and immutable.

    ``mode=ro&immutable=1`` means no locks are taken, no WAL checkpoint is ever
    triggered, and the store is never written. Every Hermes reader must use this
    so the safety contract lives in one place.
    """
    return f"file:{path}?mode=ro&immutable=1"


class TranscriptSourceAdapter:
    def read_redacted_transcript(self, request: dict) -> str:
        """Return the redacted transcript text for the request's local source.

        Raises ValueError with one of the source_* error classes
        ('source_unproven' | 'source_policy_blocked' | 'source_unreadable') so the
        drain can classify and quarantine without leaking anything.
        """
        raise NotImplementedError


class JsonlSourceAdapter(TranscriptSourceAdapter):
    """codex/claude/gemini/antigravity/grok: one jsonl file per session (opaque text)."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = _source_path(locator.get("runtime_handle"))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError("source_unreadable") from exc
        return redact_public_ingress_text(text)


class HermesSqliteSourceAdapter(TranscriptSourceAdapter):
    """hermes: one profile-local SQLite store; read-only, never written."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = _source_path(locator.get("runtime_handle"))
        session_id = str(request.get("session_id") or "")
        text = _read_hermes_session_text(path, session_id)
        return redact_public_ingress_text(text)


class OpenCodeSqliteSourceAdapter(TranscriptSourceAdapter):
    """opencode: single SQLite store; read-only, never written."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = _source_path(locator.get("runtime_handle"))
        session_id = str(request.get("session_id") or "")
        text = _read_opencode_session_text(path, session_id)
        return redact_public_ingress_text(text)


_DEFAULT_ADAPTER = JsonlSourceAdapter()
_ADAPTERS: dict[str, TranscriptSourceAdapter] = {
    "hermes": HermesSqliteSourceAdapter(),
    "opencode": OpenCodeSqliteSourceAdapter(),
}


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


def enumerate_hermes_sessions(db_path) -> list[str]:
    """Return the distinct session ids that have messages, sorted, read-only.

    Opens the store with mode=ro&immutable=1 (no locks, no WAL checkpoint, never
    written) and reads only the ``session_id`` column — never message content. When
    the messages table has no ``session_id`` column it is treated as a single-session
    store and ``[""]`` is returned. An unreadable/non-sqlite/missing store yields ``[]``.
    """
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        return []
    try:
        conn = sqlite3.connect(_hermes_ro_uri(path), uri=True)
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


def _read_hermes_session_text(path: Path, session_id: str) -> str:
    """Read one Hermes session's messages from the SQLite store, read-only.

    Opens with mode=ro&immutable=1 so it never locks the store or triggers a WAL
    checkpoint and never writes. Returns assembled "role: content" lines. The store
    is selected by session_id when that column exists; otherwise all messages are
    read (a single-session store).
    """
    try:
        conn = sqlite3.connect(_hermes_ro_uri(path), uri=True)
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
            # Multi-session store: a session id is required to isolate one session.
            # Refuse (quarantine) rather than read every session, which would leak
            # other sessions' content into one shipped chunk.
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


def _opencode_ro_uri(path: Path) -> str:
    """SQLite URI that opens the OpenCode store read-only and immutable.

    Same safety contract as Hermes: ``mode=ro&immutable=1`` means no locks are
    taken, no WAL checkpoint is ever triggered, and the store is never written.
    """
    return f"file:{path}?mode=ro&immutable=1"


def enumerate_opencode_sessions(db_path) -> list[str]:
    """Return the OpenCode session ids, sorted, read-only.

    Reads ``session_v2.id`` (fallback: distinct ``session_message.session_id``).
    Opens with mode=ro&immutable=1 (no locks, no WAL checkpoint, never written).
    An unreadable/non-sqlite/missing store yields ``[]``.
    """
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        return []
    try:
        conn = sqlite3.connect(_opencode_ro_uri(path), uri=True)
    except sqlite3.Error:
        return []
    try:
        try:
            rows = conn.execute(
                f"SELECT id FROM {OPENCODE_SESSIONS_TABLE} ORDER BY id"
            ).fetchall()
        except sqlite3.Error:
            try:
                rows = conn.execute(
                    f"SELECT DISTINCT session_id FROM {OPENCODE_MESSAGES_TABLE} "
                    "WHERE session_id IS NOT NULL ORDER BY session_id"
                ).fetchall()
            except sqlite3.Error:
                return []
    finally:
        conn.close()
    return [str(row[0]) for row in rows if row[0] is not None]


def _read_opencode_session_text(path: Path, session_id: str) -> str:
    """Read one OpenCode session's messages from the SQLite store, read-only.

    Opens with mode=ro&immutable=1 so it never locks the store or triggers a WAL
    checkpoint and never writes. Assembles "role: content" lines from user text,
    assistant text/tool blocks, and synthetic shell text; system/idle rows are
    skipped (tool catalog/outcome noise, not conversation). A missing session_id
    is refused (quarantine) rather than dumping every session.
    """
    if not session_id:
        raise ValueError("source_unreadable")
    try:
        conn = sqlite3.connect(_opencode_ro_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise ValueError("source_unreadable") from exc
    try:
        conn.row_factory = sqlite3.Row
        try:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({OPENCODE_MESSAGES_TABLE})")}
        except sqlite3.Error as exc:
            raise ValueError("source_unreadable") from exc
        if not columns or "data" not in columns:
            raise ValueError("source_unreadable")
        order_by = "seq" if "seq" in columns else "rowid"
        try:
            rows = conn.execute(
                f"SELECT type, data FROM {OPENCODE_MESSAGES_TABLE} "
                f"WHERE session_id = ? ORDER BY {order_by}",
                (session_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ValueError("source_unreadable") from exc
    finally:
        conn.close()
    lines: list[str] = []
    for row in rows:
        try:
            msg_type = str(row["type"] or "")
        except (KeyError, IndexError):
            continue
        try:
            data = row["data"]
        except (KeyError, IndexError):
            continue
        lines.extend(_opencode_message_lines(msg_type, data))
    return "\n".join(lines)


def _opencode_message_lines(msg_type: str, data) -> list[str]:
    """Extract transcript lines for one session_message row (never raises)."""
    import json as _json

    if isinstance(data, (bytes, bytearray)):
        try:
            data = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return []
    if isinstance(data, str):
        try:
            payload = _json.loads(data)
        except (TypeError, ValueError):
            return []
    elif isinstance(data, dict):
        payload = data
    else:
        return []
    if not isinstance(payload, dict):
        return []
    if msg_type == "user":
        text = payload.get("text") or ""
        text = text if isinstance(text, str) else str(text)
        return [f"user: {text}"] if text.strip() else []
    if msg_type == "assistant":
        return _opencode_assistant_lines(payload)
    if msg_type == "synthetic":
        text = payload.get("text") or ""
        text = text if isinstance(text, str) else str(text)
        return [f"synthetic: {text}"] if text.strip() else []
    # system/idle: catalog snapshots and outcome markers, not conversation.
    return []


def _opencode_assistant_lines(payload: dict) -> list[str]:
    content = payload.get("content")
    if not isinstance(content, list):
        return []
    lines: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text = block.get("text") or ""
            text = text if isinstance(text, str) else str(text)
            if text.strip():
                lines.append(f"assistant: {text}")
        elif block_type == "reasoning":
            continue
        elif block_type == "tool":
            name = str(block.get("name") or "tool")
            parts = [f"assistant [tool:{name}]"]
            state = block.get("state")
            if isinstance(state, dict):
                for key in ("input", "content"):
                    value = state.get(key)
                    rendered = _opencode_tool_value(value)
                    if rendered:
                        parts.append(rendered)
            else:
                rendered = _opencode_tool_value(block.get("content"))
                if rendered:
                    parts.append(rendered)
            lines.append(": ".join(parts))
    return lines


def _opencode_tool_value(value) -> str:
    import json as _json

    texts: list[str] = []

    def _collect(node) -> None:
        if isinstance(node, str):
            if node.strip():
                texts.append(node)
        elif isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
            for key in ("content", "input", "output"):
                if key in node:
                    _collect(node[key])
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    _collect(value)
    if not texts:
        if isinstance(value, dict):
            try:
                rendered = _json.dumps(value, ensure_ascii=False, sort_keys=True)[:2000]
            except (TypeError, ValueError):
                return ""
            return rendered if rendered.strip() else ""
        return ""
    return "\n".join(texts)
