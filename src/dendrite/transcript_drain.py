from __future__ import annotations

import hashlib
import json
import fcntl
import os
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .transcript_capture import TranscriptCaptureSpool, validate_capture_request
from .transcript_ingest import IngressQueueClient
from .transcript_outbox import TranscriptOutbox
from .transcript_source import MAX_TRANSCRIPT_BODY_CHARS, adapter_for


DRAIN_SCHEMA_VERSION = "dendrite_transcript_drain_result.v1"
PARSER_VERSION = "dendrite-thin-transcript-drain.v2"
RECOVERABLE_ERROR_CLASSES = {
    "ingress_invalid_json",
    "ingress_unreachable",
    "delivery_state_unavailable",
}
# A request that keeps failing must not block the spool head forever: after
# this many retries it is quarantined so other clients' transcripts keep
# flowing (head-of-line isolation).
MAX_DELIVERY_ATTEMPTS = 8


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
    def empty_report(**updates):
        report = _drain_transcript_spool_once(
            capture_spool=capture_spool, ingress=ingress, target_profile=target_profile, max_items=0
        )
        report["recovered_processing_count"] = 0
        report.update(updates)
        return report

    try:
        fd = os.open(capture_spool.root / ".transcript-drain.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        return empty_report(
            status="retry_pending", last_status="retry_pending", last_error_class="delivery_state_unavailable"
        )
    try:
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return empty_report(last_status="already_running")
        except OSError:
            return empty_report(
                status="retry_pending", last_status="retry_pending", last_error_class="delivery_state_unavailable"
            )
        # Every consumer holds the lock, so any remaining processing entry is
        # from an interrupted tick. Reuse its persisted snapshot/checkpoint.
        try:
            recovered = capture_spool.requeue_stale_processing(
                max_age_seconds=0, max_items=max(max_items, 0)
            )
        except OSError:
            return empty_report(
                status="retry_pending", last_status="retry_pending", last_error_class="delivery_state_unavailable"
            )
        report = _drain_transcript_spool_once(
            capture_spool=capture_spool, ingress=ingress, target_profile=target_profile,
            max_items=max_items, requeue_recoverable_quarantine=requeue_recoverable_quarantine,
        )
        report["recovered_processing_count"] = recovered
        report["mutation_performed"] = report["mutation_performed"] or bool(recovered)
        return report
    finally:
        os.close(fd)


def _drain_transcript_spool_once(
    *, capture_spool: TranscriptCaptureSpool, ingress: IngressQueueClient,
    target_profile: str, max_items: int = 5, requeue_recoverable_quarantine: bool = False,
) -> dict:
    requeued = 0
    if requeue_recoverable_quarantine:
        requeued = capture_spool.requeue_recoverable_quarantine(max_items=max(max_items, 0))
    attempted = 0
    queued = 0
    queued_parts = 0
    delivery_pending = 0
    post_budget = max(max_items, 0)
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
            outbox, snapshot_hash, documents, next_part = _prepare_delivery(
                capture_spool, claimed, request, target_profile
            )
            next_part_before = next_part
            while next_part < len(documents) and post_budget > 0:
                packed = documents[next_part]
                content_hash = _sha256(packed.body)
                network_used = True
                post_budget -= 1
                enqueue = ingress.enqueue_document(
                    source=_queue_source(request),
                    packed=packed,
                    content_hash=content_hash,
                    target_profile=target_profile,
                    kind=packed.kind,
                    idempotency_key=f"{request['provider']}:{packed.kind}:{content_hash}",
                )
                queued_parts += 1
                next_part += 1
                _checkpoint_delivery(capture_spool, claimed, snapshot_hash, next_part)
                last_status = str(enqueue.get("status") or "queued") if isinstance(enqueue, dict) else "queued"
            if next_part == len(documents):
                capture_spool.ack(claimed)
                queued += 1
                try:
                    outbox.discard(str(request["request_id"]))
                except OSError:
                    pass  # The request is already acked; leftover snapshots are safe to retain.
            else:
                # Multipart continuation. Count every requeue so a request that
                # never completes is eventually quarantined instead of retrying
                # forever (poison-pill protection for the shared spool).
                attempts = capture_spool.bump_delivery_attempt(claimed)
                if attempts >= MAX_DELIVERY_ATTEMPTS:
                    failure = _failure_record("delivery_retry_exhausted", claimed)
                    failure["recoverable"] = False
                    capture_spool.quarantine_with_failure(claimed, failure)
                    quarantined += 1
                    last_status = "quarantined"
                else:
                    capture_spool.requeue_with_failure(claimed)
                    delivery_pending += 1
                    last_status = "delivery_pending"
                break
        except Exception as exc:
            error_class = _classify_error(exc)
            failure = _failure_record(error_class, claimed)
            last_error_class = error_class
            if claimed.exists():
                if _is_recoverable_error_class(error_class):
                    if int(failure.get("retry_attempts") or 0) >= MAX_DELIVERY_ATTEMPTS:
                        exhausted = dict(failure)
                        exhausted["error_class"] = "retry_exhausted"
                        exhausted["recoverable"] = False
                        exhausted["message"] = f"{error_class} retry exhausted"
                        capture_spool.quarantine_with_failure(claimed, exhausted)
                        quarantined += 1
                        last_status = "quarantined"
                        continue
                    capture_spool.requeue_with_failure(claimed, failure)
                    retry_pending += 1
                    last_status = "retry_pending"
                    break
                capture_spool.quarantine_with_failure(claimed, failure)
                quarantined += 1
                last_status = "quarantined"
        if post_budget == 0:
            break

    status = "idle"
    if retry_pending:
        status = "retry_pending"
    elif quarantined:
        status = "quarantined"
    elif queued or queued_parts:
        status = "queued"
    elif requeued:
        status = "requeued"
    return {
        "schema_version": DRAIN_SCHEMA_VERSION,
        "status": status,
        "last_status": last_status,
        "attempted_count": attempted,
        "queued_count": queued,
        "queued_part_count": queued_parts,
        "delivery_pending_count": delivery_pending,
        "quarantined_count": quarantined,
        "retry_pending_count": retry_pending,
        "requeued_recoverable_count": requeued,
        "last_error_class": last_error_class,
        "mutation_performed": bool(attempted or requeued),
        "network_used": network_used,
        "raw_locator_printed": False,
        "raw_transcript_printed": False,
    }


def _checkpoint_delivery(
    capture_spool: TranscriptCaptureSpool, claimed: Path, snapshot_hash: str, next_part: int
) -> None:
    try:
        capture_spool.checkpoint_delivery(claimed, snapshot_hash=snapshot_hash, next_part=next_part)
    except OSError as exc:
        raise RuntimeError("delivery_state_unavailable") from exc


def _prepare_delivery(
    capture_spool: TranscriptCaptureSpool, claimed: Path, request: dict, target_profile: str
) -> tuple[TranscriptOutbox, str, list[PackedTranscriptDocument], int]:
    try:
        outbox = TranscriptOutbox(capture_spool.root / "outbox")
        snapshot = outbox.load(str(request["request_id"]))
        progress = request.get("delivery")
        if snapshot is None:
            if progress is not None:
                raise ValueError("delivery_snapshot_missing")
            snapshot = outbox.create(
                str(request["request_id"]),
                target_profile=target_profile,
                documents=[asdict(part) for part in build_drain_documents(request)],
            )
        if snapshot["target_profile"] != target_profile:
            raise ValueError("delivery_target_mismatch")
        documents = [PackedTranscriptDocument(**part) for part in snapshot["documents"]]
        next_part = 0
        if progress is not None:
            if not isinstance(progress, dict) or progress.get("snapshot_hash") != snapshot["snapshot_hash"]:
                raise ValueError("delivery_progress_invalid")
            next_part = progress.get("next_part")
            if type(next_part) is not int or not 0 <= next_part <= len(documents):
                raise ValueError("delivery_progress_invalid")
        else:
            _checkpoint_delivery(capture_spool, claimed, snapshot["snapshot_hash"], 0)
        return outbox, snapshot["snapshot_hash"], documents, next_part
    except OSError as exc:
        raise RuntimeError("delivery_state_unavailable") from exc


def build_drain_document(request: dict) -> PackedTranscriptDocument:
    """Single-document compatibility helper; refuse to silently return a prefix."""
    documents = build_drain_documents(request)
    if len(documents) != 1:
        raise ValueError("transcript_requires_multiple_parts")
    return documents[0]


def build_drain_documents(request: dict) -> list[PackedTranscriptDocument]:
    redacted_source = adapter_for(str(request["provider"])).read_redacted_transcript(request)
    observed_at = str(request.get("observed_at") or _now_iso())
    source_hash = _sha256(redacted_source)
    starts, ends = [], []
    offset = 0
    for line in redacted_source.splitlines(keepends=True):
        if line.strip():
            starts.append(offset)
            ends.append(offset + len(line))
        offset += len(line)
    turn_count = max(len(starts), 1)
    # Reserve the actual envelope with worst-case positional digit widths. The
    # budget includes JSON escaping, so Korean text and emoji also stay bounded.
    ceiling = max(len(redacted_source), 1)
    envelope = _pack_part(
        request, "", observed_at=observed_at, source_hash=source_hash,
        char_start=len(redacted_source), char_end=len(redacted_source),
        part_index=ceiling, part_count=ceiling,
        turn_start=turn_count, turn_end=turn_count,
    )
    budget = MAX_TRANSCRIPT_BODY_CHARS - len(json.dumps(envelope.body, ensure_ascii=True))
    if budget < 12:
        raise ValueError("document_envelope_too_large")
    spans = _split_source(redacted_source, budget)
    documents = []
    for index, (start, end) in enumerate(spans, 1):
        turn_start = min(bisect_right(ends, start) + 1, turn_count)
        turn_end = max(turn_start, bisect_left(starts, end))
        packed = _pack_part(
            request, redacted_source[start:end], observed_at=observed_at, source_hash=source_hash,
            char_start=start, char_end=end, part_index=index, part_count=len(spans),
            turn_start=turn_start, turn_end=turn_end,
        )
        if len(json.dumps(packed.body, ensure_ascii=True)) > MAX_TRANSCRIPT_BODY_CHARS:
            raise ValueError("document_part_too_large")
        documents.append(packed)
    return documents


def _split_source(text: str, budget: int) -> list[tuple[int, int]]:
    if not text:
        return [(0, 0)]
    spans = []
    start = 0
    while start < len(text):
        end, used = start, 0
        while end < len(text):
            char = text[end]
            code = ord(char)
            if char in ('"', "\\", "\b", "\f", "\n", "\r", "\t"):
                cost = 2
            elif code > 0xFFFF:
                cost = 12
            elif code < 32 or code >= 127:
                cost = 6
            else:
                cost = 1
            if used + cost > budget:
                break
            used += cost
            end += 1
        if end == start:
            raise ValueError("document_part_too_large")
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline >= start + (end - start) // 2:
                end = newline + 1
        spans.append((start, end))
        start = end
    return spans


def _pack_part(
    request: dict, source_part: str, *, observed_at: str, source_hash: str,
    char_start: int, char_end: int, part_index: int, part_count: int,
    turn_start: int, turn_end: int,
) -> PackedTranscriptDocument:
    provider = str(request["provider"])
    project = str(request["project"])
    locator = request.get("source_locator") or {}
    session_id_hash = str(request["session_id_hash"])
    source_locator_hash = str(locator.get("locator_hash") or "")
    identity = f"{session_id_hash}:{request['request_id']}:{source_hash}:{char_start}:{char_end}"
    chunk_id = f"conversation_{_hash_fragment(_sha256(identity), 40)}"
    agent_id = str(request.get("agent_id") or f"{_slug(provider)}-transcript-capture")
    content = "\n".join(
        [
            "# Conversation Chunk",
            "",
            "## Context",
            "",
            f"- provider: {provider}",
            f"- project: {project}",
            f"- session_id_hash: {_hash_fragment(session_id_hash, 12)}",
            f"- turn_range: {turn_start}-{turn_end}",
            f"- part: {part_index}/{part_count}",
            "- currentness: historical_conversation_memory",
            "",
            "## Transcript",
            "",
            "",
        ]
    ) + source_part
    metadata = {
        "schema_version": "agent_knowledge_document.v2",
        "result_type": "conversation_chunk",
        "knowledge_id": f"kn_{_hash_fragment(_sha256(chunk_id + content), 24)}",
        "provider": provider,
        "project": project,
        "agent_id": agent_id,
        "session_id_hash": session_id_hash,
        "source_locator_hash": source_locator_hash,
        "chunk_id": chunk_id,
        "turn_start_index": turn_start,
        "turn_end_index": turn_end,
        "part_index": part_index,
        "part_count": part_count,
        "char_start": char_start,
        "char_end": char_end,
        "capture_snapshot_hash": source_hash,
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
    if provider == "hermes" and request.get("hermes_profile"):
        metadata["hermes_profile"] = str(request["hermes_profile"])
    body = _render_markdown(metadata, content)
    content_hash = _sha256(body)
    filename = (
        f"ak-conv-{_slug(provider)}-{_slug(project)}-{_hash_fragment(session_id_hash, 12)}"
        f"-t{turn_start:04d}-{turn_end:04d}-p{part_index:06d}-of-{part_count:06d}"
        f"-{_compact_utc_timestamp(observed_at)}-{_hash_fragment(content_hash, 12)}.md"
    )
    return PackedTranscriptDocument(
        kind="conversation_chunk",
        title=f"{provider} conversation chunk {part_index}/{part_count}",
        body=body,
        metadata=metadata,
        filename=filename,
    )


def _queue_source(request: dict) -> dict[str, str]:
    source = {
        "host": "mac_mini",
        "producer": "dendrite-transcript-drain",
        "provider": str(request["provider"]),
        "project": str(request["project"]),
    }
    if request.get("provider") == "hermes" and request.get("hermes_profile"):
        source["hermes_profile"] = str(request["hermes_profile"])
    return source


def _render_markdown(metadata: dict, content: str) -> str:
    return f"---\n{_render_yaml(metadata)}---\n{content}"


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
    for marker in ("delivery_state_unavailable", "delivery_snapshot_invalid", "delivery_snapshot_missing",
                   "delivery_progress_invalid", "delivery_target_mismatch"):
        if marker in message:
            return marker
    for marker in ("source_unreadable", "source_unproven", "source_policy_blocked", "source_parse_failed"):
        if marker in message:
            return marker
    if "ingress enqueue" in message:
        if "unreachable" in message:
            return "ingress_unreachable"
        if "invalid_json" in message:
            return "ingress_invalid_json"
        return "ingress_rejected"
    if isinstance(exc, OSError):
        return "delivery_state_unavailable"
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
