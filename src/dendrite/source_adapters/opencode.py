"""OpenCode SQLite source adapter."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .base import TranscriptSourceAdapter
from .common import source_path
from ..redaction import redact_public_ingress_text

OPENCODE_MESSAGES_TABLE = "session_message"
OPENCODE_SESSIONS_TABLE = "session_v2"


def opencode_ro_uri(path: Path) -> str:
    return f"file:{path}?mode=ro&immutable=1"


class OpenCodeSqliteSourceAdapter(TranscriptSourceAdapter):
    """opencode: single SQLite store; read-only, never written."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = source_path(locator.get("runtime_handle"))
        session_id = str(request.get("session_id") or "")
        text = read_opencode_session_text(path, session_id)
        return redact_public_ingress_text(text)


def enumerate_opencode_sessions(db_path) -> list[str]:
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        return []
    try:
        conn = sqlite3.connect(opencode_ro_uri(path), uri=True)
    except sqlite3.Error:
        return []
    try:
        try:
            rows = conn.execute(f"SELECT id FROM {OPENCODE_SESSIONS_TABLE} ORDER BY id").fetchall()
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


def read_opencode_session_text(path: Path, session_id: str) -> str:
    if not session_id:
        raise ValueError("source_unreadable")
    try:
        conn = sqlite3.connect(opencode_ro_uri(path), uri=True)
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
                f"SELECT type, data FROM {OPENCODE_MESSAGES_TABLE} WHERE session_id = ? ORDER BY {order_by}",
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
        lines.extend(opencode_message_lines(msg_type, data))
    return "\n".join(lines)


def opencode_message_lines(msg_type: str, data) -> list[str]:
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
        return opencode_assistant_lines(payload)
    if msg_type == "synthetic":
        text = payload.get("text") or ""
        text = text if isinstance(text, str) else str(text)
        return [f"synthetic: {text}"] if text.strip() else []
    return []


def opencode_assistant_lines(payload: dict) -> list[str]:
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
                    rendered = opencode_tool_value(value)
                    if rendered:
                        parts.append(rendered)
            else:
                rendered = opencode_tool_value(block.get("content"))
                if rendered:
                    parts.append(rendered)
            lines.append(": ".join(parts))
    return lines


def opencode_tool_value(value) -> str:
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
