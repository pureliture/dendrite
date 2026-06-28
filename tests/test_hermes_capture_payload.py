"""Hermes provider capture tests.

Hermes Agent (Nous Research) stores all sessions in a single SQLite store
(`~/.hermes/state.db`), not per-session jsonl files. dendrite therefore treats
Hermes as a locator-only *pointer* provider: it records the store locator and
safe metadata, never opens or parses the SQLite body, and defers session body
extraction to neurons. These tests pin that contract.
"""

import hashlib
import io
import json
import sqlite3
import stat

import pytest

from dendrite.cli import main
from dendrite.provider_contracts import (
    build_default_provider_source_contracts,
    build_provider_hook_plan,
)
from dendrite.providers.contracts import no_op_hook_response, normalize_provider_event
from dendrite.transcript_capture import (
    SUPPORTED_TRANSCRIPT_PROVIDERS,
    TranscriptCaptureSpool,
    normalize_provider_capture_request,
)
from dendrite.transcript_drain import drain_transcript_spool_once

PROJECT = "dendrite"
HERMES_SESSION_ID = "hermes-sess-01HXAAAAAAAAAAAAAAAAAAAAAA"
DB_BODY_SENTINEL = "SQLITE_PRIVATE_BODY_SENTINEL_must_never_ship"


def _hermes_session_payload(state_db_path: str) -> dict:
    """Hermes session-end payload with an explicit SQLite store locator."""
    return {
        "hook_event_name": "Stop",
        "session_id": HERMES_SESSION_ID,
        "transcript_path": state_db_path,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }


def _write_fake_state_db(path) -> None:
    # A stand-in for the real SQLite store. The bytes must never reach the wire.
    path.write_text(DB_BODY_SENTINEL + "\nbinary-ish-content\n", encoding="utf-8")


class _RecordingIngress:
    """Fake IngressQueueClient that records enqueue calls (no network)."""

    def __init__(self):
        self.calls = []

    def enqueue_document(self, *, source, packed, content_hash, target_profile, kind, idempotency_key):
        self.calls.append(
            {
                "source": source,
                "packed": packed,
                "content_hash": content_hash,
                "target_profile": target_profile,
                "kind": kind,
                "idempotency_key": idempotency_key,
            }
        )
        return {"status": "queued", "job_id": "job-hermes-1"}


# --- identity -------------------------------------------------------------


def test_hermes_is_a_supported_transcript_provider():
    assert "hermes" in SUPPORTED_TRANSCRIPT_PROVIDERS


def test_hermes_contract_is_registered_but_unverified():
    contracts = {c.provider: c for c in build_default_provider_source_contracts()}
    assert "hermes" in contracts
    hermes = contracts["hermes"]
    assert hermes.hook_install_status == "deferred_not_installed"
    # Hermes has not been live-smoked and its store is SQLite, so it must not
    # claim a verified source locator.
    assert hermes.source_status != "source_locator_verified"


def test_hermes_hook_plan_is_non_mutating_and_deferred():
    plan = build_provider_hook_plan(provider="hermes", action="install")
    assert plan["live_mutation_allowed"] is False
    assert plan["hook_mutation_performed"] is False
    assert plan["mutation_performed"] is False
    # unverified source -> non-mutating blocked plan, never an auto-install
    assert plan["planned_status"] == "blocked_source_unproven"
    assert plan["requires_approval_before_execution"] is True


def test_hermes_event_normalizer_maps_session_end():
    normalized = normalize_provider_event(
        "hermes", {"hook_event_name": "Stop", "session_id": "s1"}
    )
    assert normalized["provider"] == "hermes"
    assert normalized["event_type"] == "session_end"


def test_no_op_hook_response_accepts_hermes():
    assert no_op_hook_response("hermes") == ""


# --- locator-only capture -------------------------------------------------


def test_hermes_capture_is_locator_only_from_explicit_path(tmp_path):
    db = tmp_path / "state.db"
    _write_fake_state_db(db)

    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )

    locator = request["source_locator"]
    assert request["content_policy"] == "locator_only"
    assert locator["runtime_handle"] == str(db)
    assert locator["raw_path_present"] is True
    assert locator["locator_hash"].startswith("sha256:")
    assert locator["locator_version_hash"].startswith("sha256:")
    # raw path and raw body must never reach the public surface
    public = json.dumps(request["public_summary"], sort_keys=True)
    assert str(db) not in public
    assert DB_BODY_SENTINEL not in json.dumps(request, sort_keys=True)


def test_hermes_session_hash_uses_provider_prefixed_scheme(tmp_path):
    db = tmp_path / "state.db"
    _write_fake_state_db(db)
    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )
    expected = "sha256:" + hashlib.sha256(f"hermes:{HERMES_SESSION_ID}".encode("utf-8")).hexdigest()
    assert request["session_id_hash"] == expected


def test_hermes_capture_resolves_default_db_from_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    db = home / "state.db"
    _write_fake_state_db(db)
    monkeypatch.setenv("HERMES_HOME", str(home))
    payload = {
        "hook_event_name": "Stop",
        "session_id": HERMES_SESSION_ID,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == str(db)
    assert request["source_locator"]["raw_path_present"] is True


def test_hermes_capture_yields_no_source_when_db_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "missing-home"))
    payload = {
        "hook_event_name": "Stop",
        "session_id": HERMES_SESSION_ID,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    # No fabricated locator when the store is absent.
    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_hermes_capture_does_not_fabricate_locator_for_absent_explicit_path(tmp_path):
    # An explicit transcript_path that does not exist must not be fabricated into a
    # locator: existence is verified even for the generic happy-path key.
    payload = _hermes_session_payload(str(tmp_path / "does-not-exist" / "state.db"))

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_hermes_capture_rejects_symlinked_store(tmp_path):
    real = tmp_path / "real-state.db"
    _write_fake_state_db(real)
    link = tmp_path / "state.db"
    link.symlink_to(real)
    payload = _hermes_session_payload(str(link))

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_hermes_capture_rejects_raw_transcript_content(tmp_path):
    db = tmp_path / "state.db"
    _write_fake_state_db(db)
    payload = _hermes_session_payload(str(db))
    payload["transcript"] = "user: raw private turn"

    with pytest.raises(ValueError, match="transcript content fields are not allowed"):
        normalize_provider_capture_request("hermes", payload, project=PROJECT)


def test_hermes_capture_and_drain_never_read_the_store(tmp_path, monkeypatch):
    # The real risk is dendrite reading the SQLite store at all (sqlite3 OR a plain
    # open/read). Spy on the actual read vectors and assert the store path is never
    # read during capture + drain. (sqlite3 is also checked, though dendrite never
    # imports it, to guard against a future regression that adds it.)
    import builtins
    import pathlib

    db = tmp_path / "state.db"
    _write_fake_state_db(db)
    read_targets: list[str] = []
    connect_calls: list = []
    real_open = builtins.open
    real_read_text = pathlib.Path.read_text
    real_read_bytes = pathlib.Path.read_bytes

    def spy_open(file, *a, **k):
        read_targets.append(str(file))
        return real_open(file, *a, **k)

    def spy_read_text(self, *a, **k):
        read_targets.append(str(self))
        return real_read_text(self, *a, **k)

    def spy_read_bytes(self, *a, **k):
        read_targets.append(str(self))
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "read_text", spy_read_text)
    monkeypatch.setattr(pathlib.Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: connect_calls.append((a, k)))

    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    drain_transcript_spool_once(
        capture_spool=spool,
        ingress=_RecordingIngress(),
        target_profile="ragflow-transcript-memory",
        max_items=5,
    )

    assert str(db) not in read_targets
    assert connect_calls == []


# --- drain: locator pointer ship ------------------------------------------


def test_hermes_drain_ships_locator_pointer_without_db_body(tmp_path):
    db = tmp_path / "state.db"
    _write_fake_state_db(db)
    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    ingress = _RecordingIngress()

    report = drain_transcript_spool_once(
        capture_spool=spool,
        ingress=ingress,
        target_profile="ragflow-transcript-memory",
        max_items=5,
    )

    assert report["status"] == "queued"
    assert len(ingress.calls) == 1
    call = ingress.calls[0]
    assert call["kind"] == "session_pointer"
    packed = call["packed"]
    # The SQLite body and the raw store path must never be shipped — in body OR in
    # metadata (metadata ships verbatim to the wire with no ship-time redaction).
    metadata_json = json.dumps(packed.metadata, sort_keys=True)
    for surface in (packed.body, metadata_json, json.dumps(call["source"], sort_keys=True)):
        assert DB_BODY_SENTINEL not in surface
        assert str(db) not in surface
        assert HERMES_SESSION_ID not in surface
    # The pointer must be marked a pointer, not a transcript chunk.
    assert packed.metadata.get("content_kind") == "locator_pointer"


def test_non_hermes_drain_still_packs_redacted_body(tmp_path):
    transcript = tmp_path / "codex-session.jsonl"
    transcript.write_text("turn one content\nturn two content\n", encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-session-123",
        "transcript_path": str(transcript),
        "cwd": "/Users/ddalkak/Projects/neurons",
    }
    request = normalize_provider_capture_request("codex", payload, project=PROJECT)
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    ingress = _RecordingIngress()

    drain_transcript_spool_once(
        capture_spool=spool,
        ingress=ingress,
        target_profile="ragflow-transcript-memory",
        max_items=5,
    )

    assert len(ingress.calls) == 1
    call = ingress.calls[0]
    assert call["kind"] == "conversation_chunk"
    assert "## Transcript" in call["packed"].body
    assert "turn one content" in call["packed"].body


# --- CLI ------------------------------------------------------------------


def test_cli_transcript_capture_hermes_spools_without_leaking_path(tmp_path, monkeypatch, capsys):
    db = tmp_path / "state.db"
    _write_fake_state_db(db)
    payload = _hermes_session_payload(str(db))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert (
        main(
            [
                "transcript-capture",
                "--provider",
                "hermes",
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
    assert output["provider"] == "hermes"
    assert output["source_locator_hash"].startswith("sha256:")
    assert str(db) not in output_text
    assert DB_BODY_SENTINEL not in output_text
    pending = next((tmp_path / "capture-spool" / "pending").glob("*.json"))
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
