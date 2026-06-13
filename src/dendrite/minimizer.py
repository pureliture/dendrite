from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .events import validate_event
from .redaction import redact_text

HASH_ONLY_EVENTS = {"user_prompt_seen", "tool_use_summary", "assistant_turn_summary", "session_start"}
MAX_SUMMARY_CHARS = 500


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _observed_at(raw: dict) -> str:
    return raw.get("observed_at") or datetime.now(timezone.utc).isoformat()


def _summary_text(raw: dict) -> str:
    for key in ("summary", "text", "message"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def minimize_event(raw: dict) -> dict:
    provider = raw.get("provider", "unknown")
    project = raw.get("project", "unknown")
    event_type = raw.get("event_type", "manual_note")
    session_id = raw.get("session_id", "")

    if event_type in HASH_ONLY_EVENTS:
        source = raw.get("prompt") or raw.get("text") or raw.get("summary") or repr(sorted(raw.items()))
        summary = "<hash-only>"
        content_source = source
    else:
        source = _summary_text(raw)
        summary = redact_text(source)[:MAX_SUMMARY_CHARS]
        content_source = raw.get("dedupe_key") or summary

    event = {
        "schema_version": "agent_knowledge_event.v1",
        "event_id": raw.get("event_id") or "evt_" + hashlib.sha256(repr(sorted(raw.items())).encode("utf-8")).hexdigest()[:16],
        "provider": provider,
        "project": project,
        "cwd_hint": redact_text(raw.get("cwd", "")) if raw.get("cwd") else None,
        "session_id_hash": _sha256(session_id),
        "turn_id_hash": _sha256(raw["turn_id"]) if raw.get("turn_id") else None,
        "event_type": event_type,
        "observed_at": _observed_at(raw),
        "privacy_level": raw.get("privacy_level", "normal"),
        "summary": summary,
        "content_hash": _sha256(content_source),
        "redaction_version": "redaction.v1",
    }
    return validate_event(event)
