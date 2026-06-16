import json
import hashlib
import io
import stat
from datetime import datetime

import pytest

from dendrite.cli import main
from dendrite.transcript_capture import (
    TranscriptCaptureSpool,
    has_workspace_path,
    normalize_provider_capture_request,
)
from dendrite.providers.contracts import normalize_provider_event

PROJECT = "dendrite"
CONVERSATION_ID = "ffeb1faf-78d2-4c12-803e-684d6148b1ec"


def _antigravity_stop_payload(transcript_path: str) -> dict:
    """Antigravity `Stop` hook stdin contract (camelCase).

    See docs/runbooks/ANTIGRAVITY_HOOK_SPEC.md section 6. Antigravity sends
    camelCase fields and no `hook_event_name`; `Stop` itself is the terminal signal.
    """
    return {
        "executionNum": 1,
        "terminationReason": "model_stop",
        "error": "",
        "fullyIdle": True,
        "conversationId": CONVERSATION_ID,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
        "transcriptPath": transcript_path,
        "artifactDirectoryPath": "/tmp/antigravity-artifacts",
    }


def test_antigravity_stop_payload_extracts_transcript_path_locator(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    request = normalize_provider_capture_request(
        "antigravity", _antigravity_stop_payload(str(transcript)), project=PROJECT
    )

    locator = request["source_locator"]
    assert locator["runtime_handle"] == str(transcript)
    assert locator["raw_path_present"] is True
    assert locator["locator_hash"] != ""
    assert locator["locator_version_hash"] != ""
    assert str(transcript) not in json.dumps(request["public_summary"], sort_keys=True)


def test_antigravity_capture_spools_private_locator_request(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    request = normalize_provider_capture_request(
        "antigravity", _antigravity_stop_payload(str(transcript)), project=PROJECT
    )
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")

    pending = spool.enqueue(request)
    duplicate = spool.enqueue(request)

    assert duplicate == pending
    assert pending.parent.name == "pending"
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    stored = json.loads(pending.read_text(encoding="utf-8"))
    assert stored["content_policy"] == "locator_only"
    assert stored["source_locator"]["runtime_handle"] == str(transcript)
    assert str(transcript) not in json.dumps(stored["public_summary"], sort_keys=True)

    processing = spool.claim_next()
    acked = spool.ack(processing)

    assert processing.parent.name == "processing"
    assert acked.parent.name == "acked"
    assert spool.depth_counts() == {"pending": 0, "processing": 0, "acked": 1, "quarantine": 0}


def test_cli_transcript_capture_spools_stdin_payload_without_leaking_locator(tmp_path, monkeypatch, capsys):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert (
        main(
            [
                "transcript-capture",
                "--provider",
                "antigravity",
                "--project",
                PROJECT,
                "--spool",
                str(tmp_path / "capture-spool"),
                "--stdin-json",
            ]
        )
        == 0
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["status"] == "spooled"
    assert output["source_locator_hash"].startswith("sha256:")
    assert str(transcript) not in output_text
    stored = json.loads(next((tmp_path / "capture-spool" / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert stored["source_locator"]["runtime_handle"] == str(transcript)


def test_cli_transcript_capture_best_effort_kickstarts_after_spooling(tmp_path, monkeypatch, capsys):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("dendrite.cli.subprocess.run", fake_run)

    assert (
        main(
            [
                "transcript-capture",
                "--provider",
                "antigravity",
                "--project",
                PROJECT,
                "--spool",
                str(tmp_path / "capture-spool"),
                "--stdin-json",
                "--kickstart-label",
                "com.ragflow.agent-knowledge.transcript-ingest",
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "spooled"
    assert calls
    argv = calls[0][0][0]
    assert argv[:2] == ["launchctl", "kickstart"]
    assert argv[2].endswith("/com.ragflow.agent-knowledge.transcript-ingest")


def test_antigravity_capture_rejects_raw_transcript_content(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    payload["transcript"] = "user: raw private turn"

    with pytest.raises(ValueError, match="transcript content fields are not allowed"):
        normalize_provider_capture_request("antigravity", payload, project=PROJECT)


def test_cli_transcript_capture_non_fatal_error_does_not_leak_payload(tmp_path, monkeypatch, capsys):
    payload = _antigravity_stop_payload("/tmp/transcript.jsonl")
    payload["transcript"] = "user: raw private turn"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert (
        main(
            [
                "transcript-capture",
                "--provider",
                "antigravity",
                "--project",
                PROJECT,
                "--spool",
                str(tmp_path / "capture-spool"),
                "--stdin-json",
                "--non-fatal",
            ]
        )
        == 0
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["status"] == "capture_error"
    assert output["error_class"] == "ValueError"
    assert "raw private turn" not in output_text
    assert "/tmp/transcript.jsonl" not in output_text


def test_cli_transcript_capture_can_skip_headless_payload(tmp_path, monkeypatch, capsys):
    payload = _antigravity_stop_payload("/tmp/transcript.jsonl")
    payload["workspacePaths"] = []
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert (
        main(
            [
                "transcript-capture",
                "--provider",
                "antigravity",
                "--project",
                PROJECT,
                "--spool",
                str(tmp_path / "capture-spool"),
                "--stdin-json",
                "--require-workspace-path",
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "skipped_no_workspace_path"
    assert not (tmp_path / "capture-spool").exists()


def test_antigravity_stop_payload_identifies_session_from_conversation_id(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    request = normalize_provider_capture_request(
        "antigravity", _antigravity_stop_payload(str(transcript)), project=PROJECT
    )

    # capture-layer session hash must use the same provider-prefixed scheme as the
    # chunk/parser layer so spool requests can be joined to indexed chunks by hash.
    expected = "sha256:" + hashlib.sha256(f"antigravity:{CONVERSATION_ID}".encode("utf-8")).hexdigest()
    assert request["session_id_hash"] == expected


def test_project_derived_from_cli_workspace_path(tmp_path):
    # A CLI session launched outside the fallback workspace must be labelled by its own
    # workspace directory, not the hardcoded --project fallback baked into the hook.
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    payload["workspacePaths"] = ["/Users/ddalkak/Projects/my-cli-project"]

    request = normalize_provider_capture_request("antigravity", payload, project=PROJECT)

    assert request["project"] == "my-cli-project"
    assert request["public_summary"]["project"] == "my-cli-project"


def test_project_derived_from_scalar_workspace_path_before_fallback(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    payload.pop("workspacePaths", None)
    payload["workspacePath"] = "/Users/ddalkak/Projects/neurons"

    request = normalize_provider_capture_request("codex", payload, project=PROJECT)

    assert request["project"] == "neurons"
    assert request["public_summary"]["project"] == "neurons"


def test_codex_project_derived_from_cwd_before_hardcoded_fallback(tmp_path):
    transcript = tmp_path / "codex-session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-session-123",
        "transcript_path": str(transcript),
        "cwd": "/Users/ddalkak/Projects/neurons",
    }

    request = normalize_provider_capture_request("codex", payload, project=PROJECT)

    assert request["project"] == "neurons"
    assert request["public_summary"]["project"] == "neurons"


def test_codex_project_derived_from_worktree_cwd_uses_repo_slug(tmp_path):
    transcript = tmp_path / "codex-session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-session-123",
        "transcript_path": str(transcript),
        "currentWorkingDirectory": "/Users/ddalkak/Projects/neurons/.worktrees/transcript-capture-recovery",
    }

    request = normalize_provider_capture_request("codex", payload, project=PROJECT)

    assert request["project"] == "neurons"
    assert request["public_summary"]["project"] == "neurons"


def test_provider_storage_path_is_not_used_as_project_slug(tmp_path):
    transcript = tmp_path / "codex-session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-session-123",
        "transcript_path": str(transcript),
        "workspacePaths": ["/Users/ddalkak/.codex/sessions/2026/06/02"],
    }

    request = normalize_provider_capture_request("codex", payload, project=PROJECT)

    assert request["project"] == PROJECT
    assert request["project"] != "02"


def test_provider_storage_path_does_not_override_valid_cwd(tmp_path):
    transcript = tmp_path / "codex-session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-session-123",
        "transcript_path": str(transcript),
        "workspacePaths": ["/Users/ddalkak/.gemini/antigravity-cli/brain/session/.system_generated/logs"],
        "workingDirectory": "/Users/ddalkak/Projects/neurons",
    }

    request = normalize_provider_capture_request("codex", payload, project=PROJECT)

    assert request["project"] == "neurons"


def test_project_derived_from_cli_worktree_path_uses_repo_slug(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    payload["workspacePaths"] = ["/Users/ddalkak/Projects/my-cli-project/.claude-worktrees/session-a"]

    request = normalize_provider_capture_request("antigravity", payload, project=PROJECT)

    assert request["project"] == "my-cli-project"
    assert request["public_summary"]["project"] == "my-cli-project"


def test_project_falls_back_to_arg_without_workspace_path(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    payload.pop("workspacePaths", None)

    request = normalize_provider_capture_request("antigravity", payload, project=PROJECT)

    assert request["project"] == PROJECT


def test_antigravity_capture_observed_at_falls_back_to_now(tmp_path):
    # Antigravity Stop payloads carry no event timestamp, but observed_at is a required
    # capture/event schema field consumed by the runtime-evidence ingest path. Fall back
    # to capture time so the field is never empty (and never fails schema validation).
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    request = normalize_provider_capture_request(
        "antigravity", _antigravity_stop_payload(str(transcript)), project=PROJECT
    )

    assert request["observed_at"]
    parsed = datetime.fromisoformat(request["observed_at"])
    assert parsed.tzinfo is not None
    # the same fallback must reach the public summary mirror of the field
    assert request["public_summary"]["observed_at"] == request["observed_at"]


def test_antigravity_capture_observed_at_preserves_explicit_value(tmp_path):
    # An explicit timestamp in the payload must win over the capture-time fallback.
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = _antigravity_stop_payload(str(transcript))
    payload["observed_at"] = "2026-05-10T12:03:00+09:00"

    request = normalize_provider_capture_request("antigravity", payload, project=PROJECT)

    assert request["observed_at"] == "2026-05-10T12:03:00+09:00"


def test_has_workspace_path_true_when_present():
    # Interactive agy Stop payloads carry the workspace; the global hook should capture.
    assert has_workspace_path({"workspacePaths": ["/Users/ddalkak/Projects/foo"]}) is True
    assert has_workspace_path({"cwd": "/Users/ddalkak/Projects/neurons"}) is True


def test_has_workspace_path_false_when_empty_or_absent():
    # Headless `agy --print` sends no usable workspace. The global hook (with
    # --require-workspace-path) must skip these and defer to the launch-dir shim.
    assert has_workspace_path({"workspacePaths": []}) is False
    assert has_workspace_path({}) is False
    assert has_workspace_path({"workspacePaths": [""]}) is False
    assert has_workspace_path({"workspacePaths": ["   "]}) is False
    assert has_workspace_path({"workspacePaths": ["/Users/ddalkak/.codex/sessions/2026/06/02"]}) is False


def test_antigravity_legacy_normalizer_maps_stop_payload_to_session_end():
    payload = {
        "executionNum": 1,
        "terminationReason": "model_stop",
        "fullyIdle": True,
        "conversationId": CONVERSATION_ID,
    }

    normalized = normalize_provider_event("antigravity", payload)

    assert normalized.get("event_type") == "session_end"
