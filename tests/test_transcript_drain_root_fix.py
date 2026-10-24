from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from dendrite.spool import JsonFileSpool
from dendrite.transcript_capture import TranscriptCaptureSpool
from dendrite.transcript_drain import MAX_DELIVERY_ATTEMPTS, _drain_transcript_spool_once


def _capture_spool(tmp_path: Path) -> TranscriptCaptureSpool:
    return TranscriptCaptureSpool(tmp_path / "spool")


def _raw_spool(tmp_path: Path) -> JsonFileSpool:
    return JsonFileSpool(tmp_path / "spool", subdirs=("pending", "processing", "acked", "quarantine"))


def _request() -> dict:
    return {
        "schema_version": "dendrite_transcript_capture_request.v1",
        "request_id": "req_order",
        "event_type": "session_end",
        "provider": "claude",
        "project": "neurons",
        "session_id": "sess-1",
        "observed_at": "2026-10-24T00:00:00+00:00",
        "privacy_level": "locator_only",
        "redaction_version": "redaction.v2",
        "content_policy": "locator_only",
        "capabilities": {},
        "public_summary": "",
        "source": {"locator": "session/sess-1"},
    }


def test_files_order_is_oldest_first_not_lexicographic(tmp_path: Path) -> None:
    """A requeued request must rotate to the tail instead of monopolising the head."""

    spool = _raw_spool(tmp_path)
    oldest = spool._subdir_path("pending") / "req_zzz.json"
    newest = spool._subdir_path("pending") / "req_aaa.json"
    oldest.write_text(json.dumps(_request()), encoding="utf-8")
    newest.write_text(json.dumps(_request()), encoding="utf-8")
    now = time.time()
    os.utime(oldest, (now - 60, now - 60))
    os.utime(newest, (now, now))

    assert [p.name for p in spool.files("pending")] == ["req_zzz.json", "req_aaa.json"]


def test_multipart_retry_cap_quarantines_instead_of_looping_forever(tmp_path: Path, monkeypatch) -> None:
    capture = _capture_spool(tmp_path)
    pending_dir = capture.root / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "session.jsonl"
    source.write_text("hello transcript\n", encoding="utf-8")
    from dendrite.transcript_capture import normalize_provider_capture_request

    request = normalize_provider_capture_request(
        "claude",
        {
            "hook_event_name": "Stop",
            "session_id": "root-fix-session",
            "transcript_path": str(source),
            "cwd": "/Users/example/Projects/example",
        },
        project="example",
    )
    path = pending_dir / f"{request['request_id']}.json"
    request["delivery"] = {
        "snapshot_hash": "sha256:" + "0" * 64,
        "next_part": 0,
        "attempts": MAX_DELIVERY_ATTEMPTS - 1,
    }
    path.write_text(json.dumps(request), encoding="utf-8")

    from dendrite import transcript_drain as td

    class _Doc:
        body = "part"
        kind = "transcript"

    monkeypatch.setattr(td, "_prepare_delivery", lambda *a, **k: (None, "sha256:" + "0" * 64, [_Doc(), _Doc()], 0))
    monkeypatch.setattr(td, "_checkpoint_delivery", lambda *a, **k: None)
    monkeypatch.setattr(td, "_sha256", lambda *_a, **_k: "sha256:" + "1" * 64)

    class _Ingress:
        def enqueue_document(self, **kwargs):
            return {"status": "queued"}

    report = _drain_transcript_spool_once(
        capture_spool=capture, ingress=_Ingress(), target_profile="p", max_items=1
    )

    assert report["quarantined_count"] == 1
    assert not path.exists()
    quarantined = list((capture.root / "quarantine").glob("*.json"))
    assert quarantined
    failure = json.loads(quarantined[0].read_text(encoding="utf-8"))["last_failure"]
    assert failure["error_class"] == "delivery_retry_exhausted"
    assert failure["recoverable"] is False
