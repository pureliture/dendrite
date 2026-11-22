from __future__ import annotations

import json
from pathlib import Path

from dendrite.capture_spool import TranscriptCaptureSpool
from dendrite.providers.normalize import (
    normalize_provider_capture_request,
    validate_capture_request,
)
from dendrite.transcript_drain import _drain_transcript_spool_once


def _legacy_request(source: Path) -> dict:
    """Build a legacy agent-knowledge capture request without private data."""
    request = normalize_provider_capture_request(
        "codex",
        {
            "hook_event_name": "Stop",
            "session_id": "legacy-sample",
            "transcript_path": str(source),
            "cwd": "/Users/example/Projects/dendrite",
        },
        project="dendrite",
    )
    capabilities = dict(request["capabilities"])
    capabilities["hook_called_ragflow"] = capabilities.pop("hook_called_backend")
    request["capabilities"] = capabilities
    request.pop("session_id", None)
    request["last_failure"] = {
        "error_class": "AttributeError",
        "message": "transcript ingest failed",
        "recoverable": True,
    }
    request["recovery"] = {"retry_attempts": 0}
    return request


def test_validate_legacy_request_supplies_canonical_fields() -> None:
    legacy = _legacy_request(Path("/tmp/legacy-session.jsonl"))

    validated = validate_capture_request(legacy)

    assert validated["capabilities"]["hook_called_backend"] is False
    assert validated["session_id"] == ""
    assert "hook_called_backend" not in legacy["capabilities"]


def test_legacy_quarantine_request_drains_without_attributeerror(tmp_path: Path) -> None:
    source = tmp_path / "legacy-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    request = _legacy_request(source)
    quarantine = spool.root / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    (quarantine / f"{request['request_id']}.json").write_text(
        json.dumps(request), encoding="utf-8"
    )

    class _Ingress:
        def enqueue_document(self, **kwargs):
            return {"status": "queued"}

    requeue = _drain_transcript_spool_once(
        capture_spool=spool,
        ingress=_Ingress(),
        target_profile="index-transcript-memory",
        max_items=1,
        requeue_recoverable_quarantine=True,
    )
    drain = _drain_transcript_spool_once(
        capture_spool=spool,
        ingress=_Ingress(),
        target_profile="index-transcript-memory",
        max_items=1,
    )

    assert requeue["requeued_recoverable_count"] == 1
    assert requeue["quarantined_count"] == 0
    assert drain["last_error_class"] != "AttributeError"
    assert drain["quarantined_count"] == 0
    assert requeue["queued_part_count"] + drain["queued_part_count"] >= 1
