"""OmpJsonl and jsonl adapters."""
from __future__ import annotations

from pathlib import Path

from .base import TranscriptSourceAdapter
from .common import source_path
from ..redaction import redact_public_ingress_text


class JsonlSourceAdapter(TranscriptSourceAdapter):
    """codex/claude/gemini/antigravity/grok: one jsonl file per session (opaque text)."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = source_path(locator.get("runtime_handle"))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError("source_unreadable") from exc
        return redact_public_ingress_text(text)


class OmpJsonlSourceAdapter(TranscriptSourceAdapter):
    """oh-my-pi (omp): one jsonl file per session; extract only user/assistant text."""

    def read_redacted_transcript(self, request: dict) -> str:
        locator = request.get("source_locator") or {}
        path = source_path(locator.get("runtime_handle"))
        text = read_omp_session_text(path)
        return redact_public_ingress_text(text)


def read_omp_session_text(path: Path) -> str:
    import json as _json

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError("source_unreadable") from exc
    lines: list[str] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = _json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "message":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, str):
                candidate = block
            elif isinstance(block, dict) and block.get("type") == "text":
                candidate = str(block.get("text") or "")
            else:
                continue
            if candidate.strip():
                lines.append(f"{role}: {candidate.strip()}")
    return "\n".join(lines)
