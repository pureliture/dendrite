import io
import json

from dendrite.capture import capture_event
from dendrite.cli import main
from dendrite.events import EventValidationError, validate_event
from dendrite.minimizer import minimize_event
from dendrite.redaction import redact_text


def test_redaction_removes_secret_shapes_and_private_paths():
    private_path = "/Users/ddalkak/.openclaw/" + "private/portfolio/positions.yaml"
    text = "token=live-token-value path=" + private_path

    redacted = redact_text(text)

    assert "live-token-value" not in redacted
    assert private_path not in redacted
    assert "<redacted:secret>" in redacted
    assert "<redacted:private-path>" in redacted


def test_redaction_removes_quoted_secret_assignments():
    text = 'RAGFLOW_API_KEY="live-token-value" OTHER_SECRET=\'another-live-token\''

    redacted = redact_text(text)

    assert "live-token-value" not in redacted
    assert "another-live-token" not in redacted
    assert redacted.count("<redacted:secret>") == 2


def test_minimizer_keeps_manual_note_bounded_and_redacted():
    raw = {
        "provider": "codex",
        "project": "workspace-ragflow-advisor",
        "cwd": "/Users/ddalkak/.openclaw/workspace-ragflow-advisor",
        "session_id": "session-123",
        "event_type": "manual_note",
        "text": "Remember this. TOKEN=live-token-value",
    }

    event = minimize_event(raw)

    assert event["event_type"] == "manual_note"
    assert event["session_id_hash"].startswith("sha256:")
    assert "session-123" not in str(event)
    assert "live-token-value" not in event["summary"]
    assert event["summary"].endswith("<redacted:secret>")


def test_minimizer_hashes_prompt_events_without_persisting_raw_prompt():
    raw = {
        "provider": "claude",
        "project": "workspace-ragflow-advisor",
        "session_id": "session-abc",
        "event_type": "user_prompt_seen",
        "prompt": "Here is a sensitive prompt with TOKEN=live-token-value",
    }

    event = minimize_event(raw)

    assert event["event_type"] == "user_prompt_seen"
    assert event["summary"] == "<hash-only>"
    assert "sensitive prompt" not in str(event)
    assert event["content_hash"].startswith("sha256:")


def test_validate_event_rejects_raw_payload_fields():
    event = {
        "schema_version": "agent_knowledge_event.v1",
        "event_id": "evt_bad",
        "provider": "codex",
        "project": "workspace-ragflow-advisor",
        "session_id_hash": "sha256:abc",
        "event_type": "manual_note",
        "observed_at": "2026-05-09T12:00:00+09:00",
        "privacy_level": "normal",
        "summary": "ok",
        "content_hash": "sha256:def",
        "redaction_version": "redaction.v1",
        "raw_prompt": "must not persist",
    }

    try:
        validate_event(event)
    except EventValidationError as exc:
        assert "raw_prompt" in str(exc)
    else:
        raise AssertionError("raw field was accepted")


def test_capture_event_writes_minimized_event_without_stdout_or_network(tmp_path, capsys):
    spool_dir = tmp_path / "spool"

    path = capture_event(
        {
            "provider": "codex",
            "project": "workspace-ragflow-advisor",
            "session_id": "session-123",
            "event_type": "session_end",
            "text": "Session summary TOKEN=live-token-value",
        },
        spool_root=spool_dir,
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert path.parent == spool_dir / "pending"
    assert path.exists()
    assert "live-token-value" not in path.read_text()


def test_cli_capture_fixture_spools_minimized_event_without_raw_text(tmp_path, capsys):
    fixture = tmp_path / "event.json"
    fixture.write_text(
        json.dumps(
            {
                "provider": "codex",
                "project": "workspace-ragflow-advisor",
                "session_id": "session-123",
                "event_type": "manual_note",
                "text": "Keep this TOKEN=live-token-value",
            }
        ),
        encoding="utf-8",
    )

    rc = main(["capture-fixture", "--fixture", str(fixture), "--spool", str(tmp_path / "spool")])

    output = json.loads(capsys.readouterr().out)
    stored = json.loads(next((tmp_path / "spool" / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert rc == 0
    assert output["status"] == "spooled"
    assert output["provider"] == "codex"
    assert "live-token-value" not in json.dumps(output)
    assert "live-token-value" not in json.dumps(stored)


def test_cli_capture_stdin_spools_minimized_event_with_provider_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "session_id": "session-abc",
                    "event_type": "session_end",
                    "summary": "Session ended without raw prompt.",
                }
            )
        ),
    )

    rc = main([
        "capture",
        "--provider",
        "claude",
        "--project",
        "workspace-ragflow-advisor",
        "--spool",
        str(tmp_path / "spool"),
        "--stdin-json",
    ])

    output = json.loads(capsys.readouterr().out)
    stored = json.loads(next((tmp_path / "spool" / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert rc == 0
    assert output["provider"] == "claude"
    assert output["project"] == "workspace-ragflow-advisor"
    assert stored["provider"] == "claude"
    assert stored["project"] == "workspace-ragflow-advisor"


def test_minimizer_uses_private_dedupe_key_without_persisting_it():
    raw = {
        "provider": "gemini",
        "project": "workspace-ragflow-advisor",
        "session_id": "session-123",
        "event_type": "session_end",
        "summary": "Gemini SessionEnd hook observed without prompt, context, tool, or model mutation.",
        "dedupe_key": "gemini:SessionEnd:session-123:exit",
    }

    event = minimize_event(raw)

    assert event["summary"] == "Gemini SessionEnd hook observed without prompt, context, tool, or model mutation."
    assert event["content_hash"] != "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert "dedupe_key" not in event
    assert "session-123" not in str(event)
