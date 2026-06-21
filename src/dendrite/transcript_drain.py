from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .redaction import redact_public_ingress_text
from .transcript_capture import TranscriptCaptureSpool, validate_capture_request
from .transcript_ingest import IngressQueueClient


DRAIN_SCHEMA_VERSION = "dendrite_transcript_drain_result.v1"
PARSER_VERSION = "dendrite-thin-transcript-drain.v1"
MAX_TRANSCRIPT_BODY_CHARS = 180_000
RECOVERABLE_ERROR_CLASSES = {
    "ingress_invalid_json",
    "ingress_unreachable",
}


@dataclass(frozen=True)
class PackedTranscriptDocument:
    kind: str
    title: str
    body: str
    metadata: dict
    filename: str


def drain_transcript_spool_once(
    *,
    capture_spool: TranscriptCaptureSpool,
    ingress: IngressQueueClient,
    target_profile: str,
    max_items: int = 5,
    requeue_recoverable_quarantine: bool = False,
) -> dict:
    requeued = 0
    if requeue_recoverable_quarantine:
        requeued = capture_spool.requeue_recoverable_quarantine(max_items=max(max_items, 0))
    attempted = 0
    queued = 0
    quarantined = 0
    retry_pending = 0
    network_used = False
    last_status = "idle"
    last_error_class = ""

    for _ in range(max(max_items, 0)):
        try:
            claimed = capture_spool.claim_next()
        except FileNotFoundError:
            break
        attempted += 1
        try:
            request = validate_capture_request(json.loads(claimed.read_text(encoding="utf-8")))
            packed = build_drain_document(request)
            content_hash = _sha256(packed.body)
            network_used = True
            enqueue = ingress.enqueue_document(
                source=_queue_source(request),
                packed=packed,
                content_hash=content_hash,
                target_profile=target_profile,
                kind=packed.kind,
                idempotency_key=f"{request['provider']}:{packed.kind}:{content_hash}",
            )
            capture_spool.ack(claimed)
            queued += 1
            last_status = str(enqueue.get("status") or "queued") if isinstance(enqueue, dict) else "queued"
        except Exception as exc:
            error_class = _classify_error(exc)
            failure = _failure_record(error_class, claimed)
            last_error_class = error_class
            if claimed.exists():
                if _is_recoverable_error_class(error_class):
                    capture_spool.requeue_with_failure(claimed, failure)
                    retry_pending += 1
                    last_status = "retry_pending"
                    break
                capture_spool.quarantine_with_failure(claimed, failure)
                quarantined += 1
                last_status = "quarantined"

    status = "idle"
    if queued:
        status = "queued"
    elif retry_pending:
        status = "retry_pending"
    elif quarantined:
        status = "quarantined"
    elif requeued:
        status = "requeued"
    return {
        "schema_version": DRAIN_SCHEMA_VERSION,
        "status": status,
        "last_status": last_status,
        "attempted_count": attempted,
        "queued_count": queued,
        "quarantined_count": quarantined,
        "retry_pending_count": retry_pending,
        "requeued_recoverable_count": requeued,
        "last_error_class": last_error_class,
        "mutation_performed": bool(attempted or requeued),
        "network_used": network_used,
        "raw_locator_printed": False,
        "raw_transcript_printed": False,
    }


def build_drain_document(request: dict) -> PackedTranscriptDocument:
    provider = str(request["provider"])
    project = str(request["project"])
    locator = request.get("source_locator") or {}
    source_path = _source_path(locator.get("runtime_handle"))
    redacted_source = _read_redacted_source(source_path)
    observed_at = str(request.get("observed_at") or _now_iso())
    turn_count = max(_estimated_turn_count(redacted_source), 1)
    session_id_hash = str(request["session_id_hash"])
    source_locator_hash = str(locator.get("locator_hash") or "")
    chunk_id = f"conversation_{_hash_fragment(session_id_hash, 16)}"
    content = _bounded_body(
        [
            "# Conversation Chunk",
            "",
            "## Context",
            "",
            f"- provider: {provider}",
            f"- project: {project}",
            f"- session_id_hash: {_hash_fragment(session_id_hash, 12)}",
            f"- turn_range: 1-{turn_count}",
            "- currentness: historical_conversation_memory",
            "",
            "## Transcript",
            "",
            redacted_source,
        ]
    )
    metadata = {
        "schema_version": "agent_knowledge_document.v2",
        "result_type": "conversation_chunk",
        "knowledge_id": f"kn_{_hash_fragment(_sha256(content), 24)}",
        "provider": provider,
        "project": project,
        "agent_id": f"{_slug(provider)}-transcript-capture",
        "session_id_hash": session_id_hash,
        "source_locator_hash": source_locator_hash,
        "chunk_id": chunk_id,
        "turn_start_index": 1,
        "turn_end_index": turn_count,
        "part_index": 1,
        "part_count": 1,
        "char_start": 0,
        "char_end": len(redacted_source),
        "observed_at_start": observed_at,
        "observed_at_end": observed_at,
        "privacy_level": "private",
        "redaction_version": "redaction.v2",
        "parser_version": PARSER_VERSION,
        "source_status": str(locator.get("status") or "source_locator_private_spool_only"),
        "domain": "agent_memory",
        "type": "conversation_chunk",
        "capture_request_id": str(request["request_id"]),
        "provider_source_contract": f"{provider}-transcript-source.v1",
        "ledger_contract": "agent_knowledge_ledger.v3",
        "retention_policy": "private_indefinite_until_disabled",
        "supersedes": "",
    }
    body = _render_markdown(metadata, content.splitlines())
    content_hash = _sha256(body)
    filename = (
        f"ak-conv-{_slug(provider)}-{_slug(project)}-{_hash_fragment(session_id_hash, 12)}"
        f"-t0001-{turn_count:04d}-{_compact_utc_timestamp(observed_at)}-{_hash_fragment(content_hash, 12)}.md"
    )
    return PackedTranscriptDocument(
        kind="conversation_chunk",
        title=f"{provider} conversation chunk 1-{turn_count}",
        body=body,
        metadata=metadata,
        filename=filename,
    )


def _source_path(value) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("source_unproven")
    path = Path(value)
    if path.is_symlink():
        raise ValueError("source_policy_blocked")
    if not path.exists() or not path.is_file():
        raise ValueError("source_unreadable")
    return path


def _read_redacted_source(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError("source_unreadable") from exc
    redacted = redact_public_ingress_text(text)
    if len(redacted) > MAX_TRANSCRIPT_BODY_CHARS:
        return redacted[: MAX_TRANSCRIPT_BODY_CHARS - len("\n[truncated]\n")] + "\n[truncated]\n"
    return redacted


def _estimated_turn_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        count += 1
    return count


def _queue_source(request: dict) -> dict[str, str]:
    return {
        "host": "mac_mini",
        "producer": "dendrite-transcript-drain",
        "provider": str(request["provider"]),
        "project": str(request["project"]),
    }


def _bounded_body(lines: Iterable[str]) -> str:
    body = "\n".join(lines).rstrip() + "\n"
    if len(body) <= MAX_TRANSCRIPT_BODY_CHARS:
        return body
    marker = "\n[truncated]\n"
    return body[: MAX_TRANSCRIPT_BODY_CHARS - len(marker)] + marker


def _render_markdown(metadata: dict, body_lines: Iterable[str]) -> str:
    return f"---\n{_render_yaml(metadata)}---\n{chr(10).join(body_lines).rstrip()}\n"


def _render_yaml(value: dict) -> str:
    lines = []
    for key, item in value.items():
        lines.append(f"{key}: {_yaml_scalar(item)}")
    return "\n".join(lines) + "\n"


def _yaml_scalar(value) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:/@+-")
    if all(char in safe for char in text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _classify_error(exc: Exception) -> str:
    message = str(exc)
    for marker in ("source_unreadable", "source_unproven", "source_policy_blocked", "source_parse_failed"):
        if marker in message:
            return marker
    if "ingress enqueue" in message:
        if "unreachable" in message:
            return "ingress_unreachable"
        if "invalid_json" in message:
            return "ingress_invalid_json"
        return "ingress_rejected"
    return exc.__class__.__name__


def _failure_record(error_class: str, request_path: Path | str | None = None) -> dict:
    retry_attempts = 1
    if request_path is not None:
        try:
            request = json.loads(Path(request_path).read_text(encoding="utf-8"))
            last_failure = request.get("last_failure") or {}
            retry_attempts = int(last_failure.get("retry_attempts") or 0) + 1
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            retry_attempts = 1
    return {
        "error_class": error_class,
        "message": error_class,
        "recoverable": error_class in RECOVERABLE_ERROR_CLASSES,
        "retry_attempts": retry_attempts,
        "last_attempt_at": _now_iso(),
    }


def _is_recoverable_error_class(error_class: str) -> bool:
    return error_class in RECOVERABLE_ERROR_CLASSES


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in str(value))
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "unknown"


def _hash_fragment(value: str, length: int) -> str:
    if ":" in value:
        value = value.split(":", 1)[1]
    value = "".join(char for char in value if char.lower() in "0123456789abcdef")
    if len(value) >= length:
        return value[:length].lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_utc_timestamp(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return _slug(value)[:32] or "unknown-time"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
