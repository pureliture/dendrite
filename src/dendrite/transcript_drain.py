"""Thin shipper: drain locator-only capture spool to ingress.

One tick is bounded by ``max_items``. Failure classification never
re-raises transcript content; non-recoverable failures are quarantined.
"""
from __future__ import annotations

import fcntl
import json
from .capture_spool import TranscriptCaptureSpool
from .transcript_capture import validate_capture_request
from .transcript_ingest import IngressQueueClient
from .transcript_outbox import TranscriptOutbox
from .drain_builder import PackedTranscriptDocument, build_drain_documents, queue_source
from .drain_utils import classify_error as _classify_error
from .drain_utils import failure_record as _failure_record
from .drain_utils import is_recoverable_error_class as _is_recoverable_error_class
from .drain_utils import sha256 as _sha256
from .drain_utils import slug as _slug
from .drain_utils import hash_fragment as _hash_fragment
from .drain_utils import now_iso as _now_iso
from .drain_utils import compact_utc_timestamp as _compact_utc_timestamp
 
DRAIN_SCHEMA_VERSION = "dendrite_transcript_drain_result.v1"
PARSER_VERSION = "dendrite-thin-transcript-drain.v2"
MAX_DELIVERY_ATTEMPTS = 8

__all__ = [
    "PackedTranscriptDocument",
    "DRAIN_SCHEMA_VERSION",
    "PARSER_VERSION",
    "MAX_DELIVERY_ATTEMPTS",
    "drain_transcript_spool_once",
    "build_drain_document",
    "build_drain_documents",
]


def build_drain_document(request: dict) -> PackedTranscriptDocument:
    documents = build_drain_documents(request)
    if len(documents) != 1:
        raise ValueError("transcript_requires_multiple_parts")
    return documents[0]


def build_drain_documents(request: dict) -> list[PackedTranscriptDocument]:
    from .drain_builder import build_drain_documents as _build

    return _build(request)


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
    from .drain_utils import sha256 as _sha256

    requeued = 0
    if requeue_recoverable_quarantine:
        requeued = capture_spool.requeue_recoverable_quarantine(max_items=max(max_items, 0))
    attempted = queued = queued_parts = delivery_pending = quarantined = retry_pending = 0
    post_budget = max(max_items, 0)
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
            while next_part < len(documents) and post_budget > 0:
                packed = documents[next_part]
                content_hash = _sha256(packed.body)
                network_used = True
                post_budget -= 1
                enqueue = ingress.enqueue_document(
                    source=queue_source(request),
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
                    pass
            else:
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
