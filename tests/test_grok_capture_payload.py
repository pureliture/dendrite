"""Grok Build provider capture tests.

Grok stores sessions under GROK_HOME/sessions/<encoded-cwd>/<session-id>/updates.jsonl.
Live trigger is Stop (research gate). Capture is locator-only; drain uses JsonlSourceAdapter.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dendrite.provider_contracts import (
    build_default_provider_source_contracts,
    build_provider_doctor_report,
    build_provider_hook_plan,
)
from dendrite.providers.contracts import no_op_hook_response, normalize_provider_event
from dendrite.transcript_capture import (
    SUPPORTED_TRANSCRIPT_PROVIDERS,
    TranscriptCaptureSpool,
    normalize_provider_capture_request,
)
from dendrite.transcript_drain import drain_transcript_spool_once
from dendrite.transcript_migrate import (
    MIGRATION_PROVIDERS,
    build_grok_migration_request,
    enumerate_grok_sessions,
    migrate,
)

PROJECT = "dendrite"
SESSION_ID = "019f4633-2e2b-7671-a2b9-80b160bf0a74"


def _write_updates(path: Path, body: str = '{"method":"session/update"}\n') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _stop_payload(*, transcript_path: str = "", session_id: str = SESSION_ID) -> dict:
    payload = {
        "hookEventName": "stop",
        "sessionId": session_id,
        "cwd": "/tmp",
        "workspaceRoot": "/tmp",
        "reason": "end_turn",
        "timestamp": "2026-07-09T00:00:00Z",
    }
    if transcript_path:
        payload["transcriptPath"] = transcript_path
    return payload


class _RecordingIngress:
    def __init__(self):
        self.calls = []

    def enqueue_document(self, *, source, packed, content_hash, target_profile, kind, idempotency_key):
        self.calls.append(
            {
                "source": source,
                "packed": packed,
                "content_hash": content_hash,
                "kind": kind,
            }
        )
        return {"status": "queued", "job_id": "job-grok-1"}


# --- identity -------------------------------------------------------------


def test_grok_is_supported_transcript_provider():
    assert "grok" in SUPPORTED_TRANSCRIPT_PROVIDERS
    assert "grok" in MIGRATION_PROVIDERS


def test_grok_contract_registered_deferred():
    contracts = {c.provider: c for c in build_default_provider_source_contracts()}
    assert "grok" in contracts
    grok = contracts["grok"]
    assert grok.hook_event == "Stop"
    assert grok.hook_install_status == "deferred_not_installed"
    assert grok.source_status != "source_locator_verified"


def test_grok_hook_plan_non_mutating_blocked():
    plan = build_provider_hook_plan(provider="grok", action="install")
    assert plan["live_mutation_allowed"] is False
    assert plan["hook_mutation_performed"] is False
    assert plan["planned_status"] == "blocked_source_unproven"
    assert plan["contract"]["hook_event"] == "Stop"
    # FR-D6: even blocked plans document intended ~/.grok/hooks Stop target
    cfg = plan["provider_config"]
    assert cfg["event"] == "Stop"
    assert any("~/.grok/hooks" in t for t in cfg["candidate_write_targets"])
    assert cfg["install_performed"] is False
    assert cfg["provider_config_mutation_performed"] is False


def test_grok_event_normalizer_maps_stop():
    normalized = normalize_provider_event("grok", {"hook_event_name": "Stop", "session_id": "s1"})
    assert normalized["provider"] == "grok"
    assert normalized["event_type"] == "session_end"


def test_grok_event_normalizer_accepts_camelcase_live_payload():
    """Research-gate stdin shape: hookEventName/sessionId only (no snake_case)."""
    normalized = normalize_provider_event(
        "grok",
        {"hookEventName": "stop", "sessionId": "sess-live-01", "reason": "end_turn"},
    )
    assert normalized["provider"] == "grok"
    assert normalized["event_type"] == "session_end"


def test_no_op_hook_response_accepts_grok():
    assert no_op_hook_response("grok") == ""


def test_doctor_includes_grok():
    report = build_provider_doctor_report()
    assert "grok" in report["summary"]
    assert "grok" in report["providers"]


# --- locator-only capture -------------------------------------------------


def test_grok_capture_uses_transcript_path(tmp_path):
    updates = _write_updates(tmp_path / "sessions" / "group" / SESSION_ID / "updates.jsonl")
    request = normalize_provider_capture_request(
        "grok", _stop_payload(transcript_path=str(updates)), project=PROJECT
    )
    assert request["content_policy"] == "locator_only"
    assert request["provider"] == "grok"
    assert request["event_type"] == "session_end"
    assert request["session_id"] == SESSION_ID
    assert request["source_locator"]["runtime_handle"] == str(updates)
    assert request["source_locator"]["raw_path_present"] is True
    # identity seed: provider:session_id (same pattern as other providers)
    expected_hash = "sha256:" + hashlib.sha256(f"grok:{SESSION_ID}".encode()).hexdigest()
    assert request["session_id_hash"] == expected_hash
    public = request["public_summary"]
    assert SESSION_ID not in json.dumps(public)
    assert str(updates) not in json.dumps(public)
    assert public["source_locator_hash"]
    assert "runtime_handle" not in public


def test_grok_capture_resolves_session_id_under_grok_home(tmp_path, monkeypatch):
    grok_home = tmp_path / ".grok"
    updates = _write_updates(grok_home / "sessions" / "%2Ftmp" / SESSION_ID / "updates.jsonl")
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    request = normalize_provider_capture_request("grok", _stop_payload(), project=PROJECT)
    assert request["source_locator"]["runtime_handle"] == str(updates)


def test_grok_explicit_missing_path_falls_through_to_session_resolve(tmp_path, monkeypatch):
    grok_home = tmp_path / ".grok"
    updates = _write_updates(grok_home / "sessions" / "%2Ftmp" / SESSION_ID / "updates.jsonl")
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    missing = tmp_path / "not-yet" / "updates.jsonl"
    request = normalize_provider_capture_request(
        "grok", _stop_payload(transcript_path=str(missing)), project=PROJECT
    )
    assert request["source_locator"]["runtime_handle"] == str(updates)


def test_grok_symlink_locator_falls_through_or_empty(tmp_path, monkeypatch):
    grok_home = tmp_path / ".grok"
    real = _write_updates(grok_home / "sessions" / "g" / SESSION_ID / "updates.jsonl")
    link = tmp_path / "link-updates.jsonl"
    link.symlink_to(real)
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    request = normalize_provider_capture_request(
        "grok", _stop_payload(transcript_path=str(link)), project=PROJECT
    )
    # symlink is not accepted as handle; fall through finds the real file via sessionId
    assert request["source_locator"]["runtime_handle"] == str(real)
    assert not Path(request["source_locator"]["runtime_handle"]).is_symlink()


def test_grok_short_session_id_does_not_fabricate_locator(tmp_path, monkeypatch):
    grok_home = tmp_path / ".grok"
    _write_updates(grok_home / "sessions" / "g" / "short" / "updates.jsonl")
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    request = normalize_provider_capture_request(
        "grok",
        {
            "hookEventName": "stop",
            "sessionId": "ab",  # too short for SESSION_ID_PATTERN
            "cwd": "/tmp",
        },
        project=PROJECT,
    )
    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_grok_drain_ships_conversation_chunk(tmp_path):
    updates = _write_updates(
        tmp_path / "sessions" / "g" / SESSION_ID / "updates.jsonl",
        body='{"method":"session/update","params":{"update":{"sessionUpdate":"user_message_chunk"}}}\n',
    )
    spool_root = tmp_path / "spool"
    request = normalize_provider_capture_request(
        "grok", _stop_payload(transcript_path=str(updates)), project=PROJECT
    )
    TranscriptCaptureSpool(spool_root).enqueue(request)
    ingress = _RecordingIngress()
    result = drain_transcript_spool_once(
        capture_spool=TranscriptCaptureSpool(spool_root),
        ingress=ingress,
        target_profile="test",
        max_items=5,
    )
    assert result.get("queued", 0) >= 1 or ingress.calls
    assert ingress.calls
    assert ingress.calls[0]["source"]["provider"] == "grok"
    assert ingress.calls[0]["kind"] == "conversation_chunk"


# --- migrate --------------------------------------------------------------


def test_enumerate_grok_only_updates_jsonl(tmp_path):
    root = tmp_path / "sessions"
    good = _write_updates(root / "grp" / SESSION_ID / "updates.jsonl")
    noise = root / "grp" / SESSION_ID / "chat_history.jsonl"
    noise.write_text("{}\n", encoding="utf-8")
    other = _write_updates(root / "grp" / "other-sess" / "updates.jsonl")
    found = enumerate_grok_sessions(root)
    assert set(found) == {good, other}


def test_migrate_grok_dry_run(tmp_path):
    root = tmp_path / "sessions"
    _write_updates(root / "grp" / SESSION_ID / "updates.jsonl")
    _write_updates(root / "grp" / SESSION_ID / "chat_history.jsonl")  # noise file name wrong path
    (root / "grp" / SESSION_ID / "chat_history.jsonl").write_text("x\n", encoding="utf-8")
    report = migrate(
        spool_root=tmp_path / "spool",
        roots={"grok": root},
        providers=["grok"],
        dry_run=True,
    )
    assert report["by_provider"]["grok"]["found"] == 1
    assert report["spooled"] == 1
    assert report["dry_run"] is True
    # FR-D7: counts only — no private root path or session id in report JSON
    dumped = json.dumps(report)
    assert str(root) not in dumped
    assert SESSION_ID not in dumped
    assert "root" not in report["by_provider"]["grok"]


def test_migrate_grok_spools_same_schema(tmp_path):
    root = tmp_path / "sessions"
    path = _write_updates(root / "grp" / SESSION_ID / "updates.jsonl")
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots={"grok": root}, providers=["grok"])
    assert report["spooled"] == 1
    pending = list((spool / "pending").glob("*.json"))
    assert len(pending) == 1
    req = json.loads(pending[0].read_text(encoding="utf-8"))
    assert req["provider"] == "grok"
    assert req["content_policy"] == "locator_only"
    assert req["source_locator"]["runtime_handle"] == str(path)
    assert req["session_id"] == SESSION_ID


def test_build_grok_migration_request(tmp_path):
    path = _write_updates(tmp_path / "grp" / SESSION_ID / "updates.jsonl")
    req = build_grok_migration_request(path, project=PROJECT)
    assert req["provider"] == "grok"
    assert req["session_id"] == SESSION_ID
