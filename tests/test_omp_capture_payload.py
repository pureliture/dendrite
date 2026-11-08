"""oh-my-pi (omp) provider capture tests.

omp stores sessions as jsonl files under
``~/.omp/agent/sessions/<slug>/<timestamp>_<ULID>.jsonl``. dendrite
captures locator-only (it records the session file path, never the
body, at capture time), and at drain time an OmpJsonlSourceAdapter
reads the requested file and extracts only user/assistant text from
``type == "message"`` entries. Metadata entries (``title``,
``session``, ``model_change`` …), non-conversation roles
(``toolResult``, ``developer``) and anything besides
``role in {"user","assistant"}`` / ``content[].type == "text"`` are
skipped. Unknown providers raise ``source_unproven`` and are
quarantined by the drain instead of falling back to the jsonl adapter.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from dendrite.cli import main
from dendrite.providers.contracts_def import build_default_provider_source_contracts
from dendrite.providers.doctor import build_provider_doctor_report
from dendrite.providers.hook_plan import build_provider_hook_plan
from dendrite.providers.contracts import no_op_hook_response, normalize_provider_event
from dendrite.providers.registry import adapter_kind_for
from dendrite.capture_spool import TranscriptCaptureSpool
from dendrite.providers.normalize import SUPPORTED_TRANSCRIPT_PROVIDERS, normalize_provider_capture_request
from dendrite.transcript_drain import drain_transcript_spool_once
from dendrite.drain_builder import build_drain_documents
from dendrite.migrate_runner import MIGRATION_PROVIDERS, migrate
from dendrite.drain_builder import adapter_for

PROJECT = "dendrite"
OMP_SESSION_ID = "01a1b88c-cd16-7209-892b-6c28209a254a"
SESSION_CONTENT = "hello from omp session alpha"
OTHER_CONTENT = "this tool result should not ship"


def _omp_session_payload(path: str) -> dict:
    return {
        "hook_event_name": "agent_end",
        "session_id": OMP_SESSION_ID,
        "transcript_path": path,
        "workspacePaths": ["/Users/example/Projects/dendrite"],
    }


def _write_omp_session(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {"type": "title", "v": 1, "title": "title"},
        {"type": "session", "version": 3, "id": OMP_SESSION_ID, "timestamp": "2026-11-08T00:00:00Z", "cwd": "/tmp"},
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": SESSION_CONTENT}],
                "timestamp": "2026-11-08T00:00:01Z",
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "ignored"},
                    {"type": "text", "text": "assistant reply"},
                    {"type": "toolCall", "name": "bash", "arguments": {}},
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "tc-1",
                "toolName": "bash",
                "content": [{"type": "text", "text": OTHER_CONTENT}],
                "isError": False,
            },
        },
        {"type": "message", "message": {"role": "developer", "content": [{"type": "text", "text": "ignored"}]}},
        {"type": "custom", "customType": "system-prompt-digest", "data": "ignored"},
        {"type": "custom_message", "customType": "note", "content": "ignored"},
    ]
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


# --- identity -------------------------------------------------------------


def test_omp_is_a_supported_transcript_provider():
    assert "omp" in SUPPORTED_TRANSCRIPT_PROVIDERS
    assert "omp" in MIGRATION_PROVIDERS


def test_omp_contract_is_registered_but_unverified():
    contracts = {c.provider: c for c in build_default_provider_source_contracts()}
    assert "omp" in contracts
    omp = contracts["omp"]
    assert omp.hook_event == "agent_end"
    assert omp.hook_install_status == "deferred_not_installed"
    assert omp.source_status != "source_locator_verified"


def test_omp_hook_plan_is_non_mutating_and_deferred():
    plan = build_provider_hook_plan(provider="omp", action="install")
    assert plan["live_mutation_allowed"] is False
    assert plan["hook_mutation_performed"] is False
    assert plan["mutation_performed"] is False
    assert plan["planned_status"] == "blocked_source_unproven"
    assert "provider_config" in plan
    assert plan["provider_config"]["provider"] == "omp"


def test_omp_normalizer_maps_agent_end_to_session_end():
    normalized = normalize_provider_event(
        "omp", {"hook_event_name": "agent_end", "session_id": "s1"}
    )
    assert normalized["provider"] == "omp"
    assert normalized["event_type"] == "session_end"


def test_no_op_hook_response_accepts_omp():
    assert no_op_hook_response("omp") == ""


def test_doctor_includes_omp():
    report = build_provider_doctor_report()
    assert "omp" in report["summary"]
    assert "omp" in report["providers"]


def test_cli_accepts_omp_provider(capsys):
    assert main(["provider", "hook-plan", "--provider", "omp", "--action", "install"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["provider"] == "omp"


# --- locator-only capture -------------------------------------------------


def test_omp_capture_is_locator_only(tmp_path):
    db = tmp_path / "session.jsonl"
    _write_omp_session(db)
    request = normalize_provider_capture_request(
        "omp", _omp_session_payload(str(db)), project=PROJECT
    )
    locator = request["source_locator"]
    assert locator["runtime_handle"] == str(db)
    assert request["content_policy"] == "locator_only"
    assert request["redaction_version"]
    assert SESSION_CONTENT not in json.dumps(request["public_summary"])


def test_omp_capture_absent_file_yields_no_locator(tmp_path):
    request = normalize_provider_capture_request(
        "omp", _omp_session_payload(str(tmp_path / "missing.jsonl")), project=PROJECT
    )
    assert request["source_locator"]["runtime_handle"] == ""


# --- drain / adapter ------------------------------------------------------


def test_omp_drain_extracts_only_user_assistant_text(tmp_path):
    db = tmp_path / "session.jsonl"
    _write_omp_session(db)
    request = normalize_provider_capture_request(
        "omp", _omp_session_payload(str(db)), project=PROJECT
    )
    documents = build_drain_documents(request)
    body = "\n".join(doc.body for doc in documents)
    assert SESSION_CONTENT in body
    assert "assistant reply" in body
    assert OTHER_CONTENT not in body
    assert "ignored" not in body


def test_omp_adapter_kind_is_registered():
    assert adapter_kind_for("omp") == "omp-jsonl"
    assert adapter_for("omp") is not None


def test_unknown_provider_raises_source_unproven():
    with pytest.raises(ValueError, match="source_unproven"):
        adapter_for("nonexistent-provider")


def test_omp_quarantine_path_via_drain(tmp_path):
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    request = normalize_provider_capture_request(
        "omp", _omp_session_payload(str(tmp_path / "missing.jsonl")), project=PROJECT
    )
    spool.enqueue(request)
    report = drain_transcript_spool_once(
        capture_spool=spool,
        ingress=_FakeIngress(),
        target_profile="index-transcript-memory",
        max_items=5,
    )
    assert report["quarantined_count"] == 1
    assert report["last_error_class"] == "source_unproven"


class _FakeIngress:
    def enqueue_document(self, **kwargs):
        return {"status": "queued"}
