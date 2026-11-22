from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dendrite.capture_spool import TranscriptCaptureSpool
from dendrite.transcript_drain import MAX_DELIVERY_ATTEMPTS, drain_transcript_spool_once


def _capture(request_id: str) -> dict:
    return {
        "schema_version": "agent_knowledge_capture_request.v1",
        "request_id": request_id,
        "event_type": "session_end",
        "provider": "claude",
        "project": "neurons",
        "session_id": f"sess-{request_id}",
        "session_id_hash": "sha256:" + "a" * 64,
        "observed_at": "2026-10-24T00:00:00+00:00",
        "privacy_level": "locator_only",
        "redaction_version": "redaction.v2",
        "content_policy": "locator_only",
        "capabilities": {},
        "public_summary": "",
        "source_locator": {
            "locator": f"session/{request_id}",
            "locator_hash": "sha256:" + "b" * 64,
        },
    }


def _enqueue(spool: TranscriptCaptureSpool, request_id: str, **extra) -> None:
    path = spool.enqueue(_capture(request_id))
    if extra:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(extra)
        path.write_text(json.dumps(data), encoding="utf-8")


class _Doc:
    def __init__(self, index: int):
        self.body = f"part-{index}"
        self.kind = "transcript"


class _Outbox:
    def discard(self, request_id: str) -> None:
        pass


class _Ingress:
    def __init__(self, fail_at: int | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self.fail_at = fail_at
        self.error = error or RuntimeError("ingress enqueue failed: unreachable")

    def enqueue_document(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == self.fail_at:
            raise self.error
        return {"status": "queued"}


@pytest.fixture
def parts_by_request(monkeypatch):
    """Fake N-part deliveries without writing large transcript bodies."""
    from dendrite import transcript_drain as td

    parts: dict[str, int] = {}

    def prepare(spool, claimed, request, target_profile):
        data = json.loads(claimed.read_text(encoding="utf-8"))
        next_part = int((data.get("delivery") or {}).get("next_part") or 0)
        return (
            _Outbox(),
            "sha256:" + "0" * 64,
            [_Doc(f'{request["request_id"]}:{i}') for i in range(parts[request["request_id"]])],
            next_part,
        )

    def checkpoint(spool, claimed, snapshot_hash, next_part):
        data = json.loads(claimed.read_text(encoding="utf-8"))
        data["delivery"] = {"snapshot_hash": snapshot_hash, "next_part": next_part}
        claimed.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(td, "_prepare_delivery", prepare)
    monkeypatch.setattr(td, "_checkpoint_delivery", checkpoint)
    monkeypatch.setattr(td, "_sha256", lambda body: "sha256:" + hashlib.sha256(body.encode()).hexdigest())
    return parts


def test_max_items_limits_items_not_parts(tmp_path: Path, parts_by_request) -> None:
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    for index in range(3):
        request_id = f"req{index}"
        _enqueue(spool, request_id)
        parts_by_request[request_id] = 5
    ingress = _Ingress()

    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="p", max_items=3
    )

    assert report["attempted_count"] == 3
    assert report["queued_count"] == 3
    assert report["queued_part_count"] == 15
    assert report["delivery_pending_count"] == 0
    assert report["quarantined_count"] == 0
    assert spool.depth_counts()["acked"] == 3
    assert len({call["idempotency_key"] for call in ingress.calls}) == 15


def test_multiple_ticks_finish_large_item_without_quarantine(tmp_path: Path, parts_by_request) -> None:
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    _enqueue(spool, "large")
    parts_by_request["large"] = 5
    ingress = _Ingress()

    first = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="p",
        max_items=1, max_parts_per_tick=2,
    )
    assert first["queued_part_count"] == 2
    assert first["queued_count"] == 0
    assert first["delivery_pending_count"] == 1
    pending = next((spool.root / "pending").glob("*.json"))
    progress = json.loads(pending.read_text(encoding="utf-8"))
    assert progress["delivery"]["next_part"] == 2
    assert "attempts" not in progress["delivery"]
    assert progress["last_failure"] == {
        "error_class": "delivery_part_budget_exhausted",
        "message": "delivery_part_budget_exhausted",
        "recoverable": True,
        "retry_attempts": 0,
        "last_attempt_at": progress["last_failure"]["last_attempt_at"],
        "budget_deferred": True,
    }

    ticks = 1
    while spool.depth_counts()["acked"] == 0 and ticks < 5:
        report = drain_transcript_spool_once(
            capture_spool=spool, ingress=ingress, target_profile="p",
            max_items=1, max_parts_per_tick=2,
        )
        assert report["queued_part_count"] <= 2
        assert report["quarantined_count"] == 0
        ticks += 1

    assert spool.depth_counts() == {"pending": 0, "processing": 0, "acked": 1, "quarantine": 0}
    assert len(ingress.calls) == 5
    assert len({call["idempotency_key"] for call in ingress.calls}) == 5


def test_real_error_keeps_budget_deferral_marker(tmp_path: Path, parts_by_request) -> None:
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    _enqueue(spool, "mixed")
    parts_by_request["mixed"] = 5
    ingress = _Ingress()
    drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="p",
        max_items=1, max_parts_per_tick=2,
    )
    ingress.fail_at = 3
    ingress.error = RuntimeError("ingress enqueue rejected: status=policy_blocked")

    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="p",
        max_items=1, max_parts_per_tick=5,
    )

    assert report["quarantined_count"] == 1
    quarantined = next((spool.root / "quarantine").glob("*.json"))
    failure = json.loads(quarantined.read_text(encoding="utf-8"))["last_failure"]
    assert failure["error_class"] == "ingress_rejected"
    assert failure["budget_deferred"] is True
    assert failure["retry_attempts"] == 1


def test_real_recoverable_error_retries_then_quarantines(tmp_path: Path, parts_by_request) -> None:
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    _enqueue(spool, "retry-me", last_failure={
        "error_class": "ingress_unreachable",
        "message": "ingress_unreachable",
        "recoverable": True,
        "retry_attempts": MAX_DELIVERY_ATTEMPTS - 1,
    })
    parts_by_request["retry-me"] = 1
    ingress = _Ingress(fail_at=1)

    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="p", max_items=1
    )

    assert report["status"] == "quarantined"
    quarantined = next((spool.root / "quarantine").glob("*.json"))
    failure = json.loads(quarantined.read_text(encoding="utf-8"))["last_failure"]
    assert failure["error_class"] == "retry_exhausted"
    assert failure["recoverable"] is False
    assert failure["retry_attempts"] == MAX_DELIVERY_ATTEMPTS
