"""Hermes provider capture tests.

Hermes Agent (Nous Research) stores all sessions in one local SQLite store
(`~/.hermes/state.db`), not per-session jsonl files. dendrite captures locator-only
(it records the store path, never the body, at capture time), and at drain time a
HermesSqliteSourceAdapter opens the store read-only/immutable, selects the one
session, and ships the same redacted `conversation_chunk` as the jsonl providers.
These tests pin that contract.
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
SESSION_CONTENT = "hello from hermes session alpha"
OTHER_SESSION_ID = "hermes-sess-OTHERBBBBBBBBBBBBBBBBBBBBBB"
OTHER_CONTENT = "this belongs to a different session beta"
SECRET_PATH = "/Users/ddalkak/private/secret-token-path"


def _hermes_session_payload(state_db_path: str) -> dict:
    """Hermes session-end payload with an explicit SQLite store locator."""
    return {
        "hook_event_name": "on_session_end",
        "session_id": HERMES_SESSION_ID,
        "transcript_path": state_db_path,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }


def _write_hermes_state_db(path, *, session_id=HERMES_SESSION_ID, messages=None, other=None) -> None:
    """Create a stand-in Hermes SQLite store (sessions + messages tables)."""
    if messages is None:
        messages = [("user", SESSION_CONTENT), ("assistant", "ok")]
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at INTEGER)")
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, role TEXT, content TEXT, timestamp INTEGER)"
        )
        conn.execute("INSERT INTO sessions (id, started_at) VALUES (?, ?)", (session_id, 1))
        for i, (role, content) in enumerate(messages):
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, role, content, i),
            )
        if other is not None:
            other_id, other_messages = other
            conn.execute("INSERT INTO sessions (id, started_at) VALUES (?, ?)", (other_id, 2))
            for i, (role, content) in enumerate(other_messages):
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    (other_id, role, content, 100 + i),
                )
        conn.commit()
    finally:
        conn.close()


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
    # Not live-smoked against a real Hermes install: must not claim a verified locator.
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
        "hermes", {"hook_event_name": "on_session_end", "session_id": "s1"}
    )
    assert normalized["provider"] == "hermes"
    assert normalized["event_type"] == "session_end"


def test_no_op_hook_response_accepts_hermes():
    assert no_op_hook_response("hermes") == ""


# --- locator-only capture -------------------------------------------------


def test_hermes_capture_is_locator_only(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(db)

    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )

    locator = request["source_locator"]
    assert request["content_policy"] == "locator_only"
    assert locator["runtime_handle"] == str(db)
    assert locator["raw_path_present"] is True
    assert locator["locator_hash"].startswith("sha256:")
    assert locator["locator_version_hash"].startswith("sha256:")
    # capture never reads the store body, and the raw path stays out of public surfaces
    assert SESSION_CONTENT not in json.dumps(request, sort_keys=True)
    assert str(db) not in json.dumps(request["public_summary"], sort_keys=True)


def test_hermes_capture_keeps_raw_session_id_private(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(db)
    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )
    # raw session id is kept (privately) so the SQLite adapter can select the session,
    # but it must never appear in the public summary.
    assert request["session_id"] == HERMES_SESSION_ID
    assert HERMES_SESSION_ID not in json.dumps(request["public_summary"], sort_keys=True)


def test_hermes_session_hash_uses_provider_prefixed_scheme(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(db)
    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(db)), project=PROJECT
    )
    expected = "sha256:" + hashlib.sha256(f"hermes:{HERMES_SESSION_ID}".encode("utf-8")).hexdigest()
    assert request["session_id_hash"] == expected


def test_hermes_capture_resolves_default_db_from_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    db = home / "state.db"
    _write_hermes_state_db(db)
    monkeypatch.setenv("HERMES_HOME", str(home))
    payload = {
        "hook_event_name": "on_session_end",
        "session_id": HERMES_SESSION_ID,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == str(db)
    assert request["source_locator"]["raw_path_present"] is True


def test_hermes_capture_yields_no_source_when_db_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "missing-home"))
    payload = {
        "hook_event_name": "on_session_end",
        "session_id": HERMES_SESSION_ID,
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_hermes_capture_does_not_fabricate_locator_for_absent_explicit_path(tmp_path):
    payload = _hermes_session_payload(str(tmp_path / "does-not-exist" / "state.db"))

    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_hermes_capture_rejects_symlinked_store(tmp_path):
    real = tmp_path / "real-state.db"
    _write_hermes_state_db(real)
    link = tmp_path / "state.db"
    link.symlink_to(real)

    request = normalize_provider_capture_request(
        "hermes", _hermes_session_payload(str(link)), project=PROJECT
    )

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_hermes_capture_rejects_raw_transcript_content(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(db)
    payload = _hermes_session_payload(str(db))
    payload["transcript"] = "user: raw private turn"

    with pytest.raises(ValueError, match="transcript content fields are not allowed"):
        normalize_provider_capture_request("hermes", payload, project=PROJECT)


# --- drain: SQLite adapter -> conversation_chunk --------------------------


def _capture_and_drain(tmp_path, db):
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
    return report, ingress


def test_hermes_drain_ships_conversation_chunk_for_the_session(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(
        db,
        messages=[("user", SESSION_CONTENT), ("assistant", "ok")],
        other=(OTHER_SESSION_ID, [("user", OTHER_CONTENT)]),
    )

    report, ingress = _capture_and_drain(tmp_path, db)

    assert report["status"] == "queued"
    assert len(ingress.calls) == 1
    call = ingress.calls[0]
    # Hermes ships the SAME document kind as the jsonl providers — neurons accepts it.
    assert call["kind"] == "conversation_chunk"
    body = call["packed"].body
    assert "## Transcript" in body
    assert SESSION_CONTENT in body
    # only the requested session is shipped, not other sessions in the same store
    assert OTHER_CONTENT not in body
    # raw store path never reaches the shipped surfaces
    metadata_json = json.dumps(call["packed"].metadata, sort_keys=True)
    for surface in (body, metadata_json, json.dumps(call["source"], sort_keys=True)):
        assert str(db) not in surface
        assert HERMES_SESSION_ID not in surface


def test_hermes_drain_redacts_secrets_from_db_content(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(db, messages=[("user", f"see {SECRET_PATH} please"), ("assistant", "ok")])

    _report, ingress = _capture_and_drain(tmp_path, db)

    body = ingress.calls[0]["packed"].body
    # the private path from the message content must be redacted before shipping
    assert SECRET_PATH not in body


def test_hermes_drain_reads_store_read_only_and_does_not_modify(tmp_path):
    db = tmp_path / "state.db"
    _write_hermes_state_db(db)
    before = db.stat()

    report, ingress = _capture_and_drain(tmp_path, db)

    after = db.stat()
    assert report["status"] == "queued"
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    # no rollback journal / WAL sidecar files were created by our read
    assert not (tmp_path / "state.db-wal").exists()
    assert not (tmp_path / "state.db-journal").exists()


def test_hermes_drain_refuses_when_session_id_missing_in_multisession_store(tmp_path):
    # If the store holds many sessions but the request has no session_id, dendrite must
    # NOT dump every session — refuse (quarantine), never a cross-session leak.
    db = tmp_path / "state.db"
    _write_hermes_state_db(
        db,
        messages=[("user", SESSION_CONTENT)],
        other=(OTHER_SESSION_ID, [("user", OTHER_CONTENT)]),
    )
    payload = {
        "hook_event_name": "on_session_end",
        # deliberately no session_id
        "transcript_path": str(db),
        "workspacePaths": ["/Users/ddalkak/Projects/dendrite"],
    }
    request = normalize_provider_capture_request("hermes", payload, project=PROJECT)
    assert request["session_id"] == ""
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    ingress = _RecordingIngress()

    report = drain_transcript_spool_once(
        capture_spool=spool,
        ingress=ingress,
        target_profile="ragflow-transcript-memory",
        max_items=5,
    )

    assert ingress.calls == []
    assert report["status"] == "quarantined"
    assert spool.depth_counts()["quarantine"] == 1


def test_hermes_drain_quarantines_unreadable_store(tmp_path):
    # A store that is not a valid SQLite db must be quarantined, not crash the drain.
    db = tmp_path / "state.db"
    db.write_text("not a sqlite database at all\n", encoding="utf-8")
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

    assert ingress.calls == []
    assert report["status"] == "quarantined"
    assert spool.depth_counts()["quarantine"] == 1


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
    _write_hermes_state_db(db)
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
    assert HERMES_SESSION_ID not in output_text
    pending = next((tmp_path / "capture-spool" / "pending").glob("*.json"))
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
