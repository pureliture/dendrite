"""OpenCode provider capture tests.

OpenCode stores sessions in a single SQLite store
(`~/.local/share/opencode/opencode.db`, tables ``session_v2`` + ``session_message``),
not per-session jsonl files. dendrite captures locator-only (it records the store path,
never the body, at capture time), and at drain time an OpenCodeSqliteSourceAdapter
opens the store read-only/immutable, selects the one session, and ships the same
redacted `conversation_chunk` as the jsonl providers. These tests pin that contract.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
from pathlib import Path

import pytest

from dendrite.cli import main
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
    build_opencode_migration_request,
    migrate,
)
from dendrite.transcript_source import enumerate_opencode_sessions

PROJECT = "dendrite"
OPENCODE_SESSION_ID = "ses_ea14e6bd1ffeWfaj4Qog2lYMlY"
SESSION_CONTENT = "hello from opencode session alpha"
OTHER_SESSION_ID = "ses_ea145cd20ffem3haHf5KDV3VQK"
OTHER_CONTENT = "this belongs to a different session beta"
SECRET_PATH = "/Users/example/private/secret-token-path"


def _opencode_session_payload(state_db_path: str) -> dict:
    """OpenCode session.idle payload with an explicit SQLite store locator."""
    return {
        "hook_event_name": "session.idle",
        "session_id": OPENCODE_SESSION_ID,
        "transcript_path": state_db_path,
        "workspacePaths": ["/Users/example/Projects/dendrite"],
    }


def _write_opencode_db(
    path: Path, *, session_id: str = OPENCODE_SESSION_ID, messages=None, other=None
) -> None:
    """Create a stand-in OpenCode store (session_v2 + session_message tables)."""
    import time as _time

    if messages is None:
        messages = [
            ("user", {"text": SESSION_CONTENT}),
            ("assistant", {"content": [{"type": "text", "text": "ok"}]}),
        ]
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT, directory TEXT, title TEXT)"
        )
        conn.execute(
            "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "type TEXT NOT NULL, seq INTEGER NOT NULL, time_created INTEGER, time_updated INTEGER, "
            "data TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO session_v2 (id, project_id, directory, title) VALUES (?, ?, ?, ?)",
            (session_id, "proj-1", "/tmp", "alpha"),
        )
        now = int(_time.time() * 1000)
        for i, (msg_type, data) in enumerate(messages):
            conn.execute(
                "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"msg-{session_id}-{i}", session_id, msg_type, i, now, now, json.dumps(data)),
            )
        if other is not None:
            other_id, other_messages = other
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, directory, title) VALUES (?, ?, ?, ?)",
                (other_id, "proj-1", "/tmp", "beta"),
            )
            for i, (msg_type, data) in enumerate(other_messages):
                conn.execute(
                    "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"msg-{other_id}-{i}", other_id, msg_type, i, now, now, json.dumps(data)),
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
        return {"status": "queued", "job_id": "job-opencode-1"}


# --- identity -------------------------------------------------------------


def test_opencode_is_a_supported_transcript_provider():
    assert "opencode" in SUPPORTED_TRANSCRIPT_PROVIDERS
    assert "opencode" in MIGRATION_PROVIDERS


def test_opencode_contract_is_registered_but_unverified():
    contracts = {c.provider: c for c in build_default_provider_source_contracts()}
    assert "opencode" in contracts
    opencode = contracts["opencode"]
    assert opencode.hook_event == "session.idle"
    assert opencode.hook_install_status == "deferred_not_installed"
    # Not live-smoked against a real OpenCode install: must not claim a verified locator.
    assert opencode.source_status != "source_locator_verified"


def test_opencode_hook_plan_is_non_mutating_and_deferred():
    plan = build_provider_hook_plan(provider="opencode", action="install")
    assert plan["live_mutation_allowed"] is False
    assert plan["hook_mutation_performed"] is False
    assert plan["mutation_performed"] is False
    # unverified source -> non-mutating blocked plan, never an auto-install
    assert plan["planned_status"] == "blocked_source_unproven"
    assert plan["requires_approval_before_execution"] is True
    cfg = plan["provider_config"]
    assert cfg["event"] == "session.idle"
    assert any("plugins" in t for t in cfg["candidate_write_targets"])
    assert cfg["install_performed"] is False
    assert cfg["provider_config_mutation_performed"] is False


def test_opencode_event_normalizer_maps_session_idle():
    normalized = normalize_provider_event(
        "opencode", {"hook_event_name": "session.idle", "session_id": "s1"}
    )
    assert normalized["provider"] == "opencode"
    assert normalized["event_type"] == "session_end"


def test_opencode_event_normalizer_accepts_session_id_casing():
    normalized = normalize_provider_event(
        "opencode", {"hook_event_name": "session.idle", "sessionID": "sess-live-01"}
    )
    assert normalized["provider"] == "opencode"
    assert normalized["event_type"] == "session_end"


def test_no_op_hook_response_accepts_opencode():
    assert no_op_hook_response("opencode") == ""


def test_doctor_includes_opencode():
    report = build_provider_doctor_report()
    assert "opencode" in report["summary"]
    assert "opencode" in report["providers"]


# --- locator-only capture -------------------------------------------------


def test_opencode_capture_is_locator_only(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)

    request = normalize_provider_capture_request(
        "opencode", _opencode_session_payload(str(db)), project=PROJECT
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


def test_opencode_capture_keeps_raw_session_id_private(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    request = normalize_provider_capture_request(
        "opencode", _opencode_session_payload(str(db)), project=PROJECT
    )
    # raw session id is kept (privately) so the SQLite adapter can select the session,
    # but it must never appear in the public summary.
    assert request["session_id"] == OPENCODE_SESSION_ID
    assert OPENCODE_SESSION_ID not in json.dumps(request["public_summary"], sort_keys=True)


def test_opencode_session_hash_uses_provider_prefixed_scheme(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    request = normalize_provider_capture_request(
        "opencode", _opencode_session_payload(str(db)), project=PROJECT
    )
    expected = "sha256:" + hashlib.sha256(f"opencode:{OPENCODE_SESSION_ID}".encode("utf-8")).hexdigest()
    assert request["session_id_hash"] == expected


def test_opencode_capture_resolves_explicit_db_path_env(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    monkeypatch.setenv("OPENCODE_DB_PATH", str(db))
    payload = {
        "hook_event_name": "session.idle",
        "sessionID": OPENCODE_SESSION_ID,
        "workspacePaths": ["/Users/example/Projects/dendrite"],
    }

    request = normalize_provider_capture_request("opencode", payload, project=PROJECT)

    assert request["session_id"] == OPENCODE_SESSION_ID
    assert request["source_locator"]["runtime_handle"] == str(db)
    assert request["source_locator"]["raw_path_present"] is True


def test_opencode_capture_yields_no_source_when_db_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB_PATH", str(tmp_path / "missing" / "opencode.db"))
    payload = {
        "hook_event_name": "session.idle",
        "session_id": OPENCODE_SESSION_ID,
        "workspacePaths": ["/Users/example/Projects/dendrite"],
    }

    request = normalize_provider_capture_request("opencode", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_opencode_capture_does_not_fabricate_locator_for_absent_explicit_path(tmp_path):
    payload = _opencode_session_payload(str(tmp_path / "does-not-exist" / "opencode.db"))

    request = normalize_provider_capture_request("opencode", payload, project=PROJECT)

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_opencode_capture_rejects_symlinked_store(tmp_path):
    real = tmp_path / "real-opencode.db"
    _write_opencode_db(real)
    link = tmp_path / "opencode.db"
    link.symlink_to(real)

    request = normalize_provider_capture_request(
        "opencode", _opencode_session_payload(str(link)), project=PROJECT
    )

    assert request["source_locator"]["runtime_handle"] == ""
    assert request["source_locator"]["raw_path_present"] is False


def test_opencode_capture_rejects_raw_transcript_content(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    payload = _opencode_session_payload(str(db))
    payload["transcript"] = "user: raw private turn"

    with pytest.raises(ValueError, match="transcript content fields are not allowed"):
        normalize_provider_capture_request("opencode", payload, project=PROJECT)


# --- drain: SQLite adapter -> conversation_chunk --------------------------


def _capture_and_drain(tmp_path, db):
    request = normalize_provider_capture_request(
        "opencode", _opencode_session_payload(str(db)), project=PROJECT
    )
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    ingress = _RecordingIngress()
    report = drain_transcript_spool_once(
        capture_spool=spool,
        ingress=ingress,
        target_profile="index-transcript-memory",
        max_items=5,
    )
    return report, ingress


def test_opencode_drain_ships_conversation_chunk_for_the_session(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(
        db,
        messages=[
            ("user", {"text": SESSION_CONTENT}),
            ("assistant", {"content": [{"type": "text", "text": "ok"}]}),
        ],
        other=(OTHER_SESSION_ID, [("user", {"text": OTHER_CONTENT})]),
    )

    report, ingress = _capture_and_drain(tmp_path, db)

    assert report["status"] == "queued"
    assert len(ingress.calls) == 1
    call = ingress.calls[0]
    # OpenCode ships the SAME document kind as the other providers — neurons accepts it.
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
        assert OPENCODE_SESSION_ID not in surface


def test_opencode_drain_includes_tool_block_names(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(
        db,
        messages=[
            (
                "assistant",
                {
                    "content": [
                        {
                            "type": "tool",
                            "name": "shell",
                            "state": {"status": "completed", "input": {"command": "ls"}},
                        }
                    ]
                },
            ),
        ],
    )

    _report, ingress = _capture_and_drain(tmp_path, db)

    assert "shell" in ingress.calls[0]["packed"].body


def test_opencode_drain_skips_system_and_idle_rows(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT, directory TEXT, title TEXT)")
        conn.execute(
            "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "type TEXT NOT NULL, seq INTEGER NOT NULL, time_created INTEGER, time_updated INTEGER, data TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO session_v2 (id, project_id, directory, title) VALUES (?, ?, ?, ?)",
            (OPENCODE_SESSION_ID, "proj-1", "/tmp", "t"),
        )
        conn.execute(
            "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m0", OPENCODE_SESSION_ID, "system", 0, 1, 1, json.dumps({"text": "catalog-noise-marker"})),
        )
        conn.execute(
            "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m1", OPENCODE_SESSION_ID, "user", 1, 2, 2, json.dumps({"text": SESSION_CONTENT})),
        )
        conn.execute(
            "INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m2", OPENCODE_SESSION_ID, "idle", 2, 3, 3, json.dumps({"outcome": "succeeded"})),
        )
        conn.commit()
    finally:
        conn.close()

    _report, ingress = _capture_and_drain(tmp_path, db)

    body = ingress.calls[0]["packed"].body
    assert SESSION_CONTENT in body
    assert "catalog-noise-marker" not in body


def test_opencode_drain_redacts_secrets_from_db_content(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(
        db,
        messages=[
            ("user", {"text": f"see {SECRET_PATH} please"}),
            ("assistant", {"content": [{"type": "text", "text": "ok"}]}),
        ],
    )

    _report, ingress = _capture_and_drain(tmp_path, db)

    body = ingress.calls[0]["packed"].body
    # the private path from the message content must be redacted before shipping
    assert SECRET_PATH not in body


def test_opencode_drain_reads_store_read_only_and_does_not_modify(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    before = db.stat()

    report, ingress = _capture_and_drain(tmp_path, db)

    after = db.stat()
    assert report["status"] == "queued"
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    # no rollback journal / WAL sidecar files were created by our read
    assert not (tmp_path / "opencode.db-wal").exists()
    assert not (tmp_path / "opencode.db-journal").exists()


def test_opencode_drain_refuses_when_session_id_missing(tmp_path):
    # The store holds sessions but the request has no session_id: dendrite must
    # NOT dump every session — refuse (quarantine), never a cross-session leak.
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    payload = {
        "hook_event_name": "session.idle",
        # deliberately no session_id
        "transcript_path": str(db),
        "workspacePaths": ["/Users/example/Projects/dendrite"],
    }
    request = normalize_provider_capture_request("opencode", payload, project=PROJECT)
    assert request["session_id"] == ""
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    ingress = _RecordingIngress()

    report = drain_transcript_spool_once(
        capture_spool=spool,
        ingress=ingress,
        target_profile="index-transcript-memory",
        max_items=5,
    )

    assert ingress.calls == []
    assert report["status"] == "quarantined"
    assert spool.depth_counts()["quarantine"] == 1


def test_opencode_drain_quarantines_unreadable_store(tmp_path):
    # A store that is not a valid SQLite db must be quarantined, not crash the drain.
    db = tmp_path / "opencode.db"
    db.write_text("not a sqlite database at all\n", encoding="utf-8")
    request = normalize_provider_capture_request(
        "opencode", _opencode_session_payload(str(db)), project=PROJECT
    )
    spool = TranscriptCaptureSpool(tmp_path / "capture-spool")
    spool.enqueue(request)
    ingress = _RecordingIngress()

    report = drain_transcript_spool_once(
        capture_spool=spool,
        ingress=ingress,
        target_profile="index-transcript-memory",
        max_items=5,
    )

    assert ingress.calls == []
    assert report["status"] == "quarantined"
    assert spool.depth_counts()["quarantine"] == 1


# --- migrate ------------------------------------------------------------------


def test_enumerate_opencode_sessions_sorted(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO session_v2 (id) VALUES ('ses-b'), ('ses-a')")
        conn.commit()
    finally:
        conn.close()
    assert enumerate_opencode_sessions(db) == ["ses-a", "ses-b"]


def test_enumerate_opencode_sessions_empty_for_non_sqlite(tmp_path):
    db = tmp_path / "opencode.db"
    db.write_text("not a sqlite database\n", encoding="utf-8")
    assert enumerate_opencode_sessions(db) == []


def test_enumerate_opencode_sessions_is_read_only(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    before = db.stat()
    enumerate_opencode_sessions(db)
    after = db.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


def test_migrate_opencode_dry_run_counts_sessions(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db, other=(OTHER_SESSION_ID, [("user", {"text": OTHER_CONTENT})]))
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots={"opencode": db}, providers=["opencode"], dry_run=True)
    assert report["by_provider"]["opencode"]["found"] == 2
    assert report["dry_run"] is True
    assert not spool.exists() or not list((spool / "pending").glob("*.json"))


def test_migrate_opencode_spools_per_session_locator_only(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db, other=(OTHER_SESSION_ID, [("user", {"text": OTHER_CONTENT})]))
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots={"opencode": db}, providers=["opencode"], limit=2)
    assert report["by_provider"]["opencode"]["spooled"] == 2

    pending = sorted((spool / "pending").glob("*.json"))
    assert len(pending) == 2
    sids = set()
    for f in pending:
        req = json.loads(f.read_text(encoding="utf-8"))
        assert req["provider"] == "opencode"
        assert req["content_policy"] == "locator_only"
        assert req["source_locator"]["runtime_handle"] == str(db)
        sids.add(req["session_id"])
        assert str(db) not in json.dumps(req["public_summary"], sort_keys=True)
    assert sids == {OPENCODE_SESSION_ID, OTHER_SESSION_ID}


def test_migrate_opencode_report_is_path_and_session_id_free(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db, other=(OTHER_SESSION_ID, [("user", {"text": OTHER_CONTENT})]))
    report = migrate(spool_root=tmp_path / "spool", roots={"opencode": db}, providers=["opencode"], limit=2)
    opencode = report["by_provider"]["opencode"]
    assert "root" not in opencode
    blob = json.dumps(report)
    assert str(db) not in blob
    assert OPENCODE_SESSION_ID not in blob
    assert OTHER_SESSION_ID not in blob


def test_migrate_opencode_blocks_unbounded_non_dry_run(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    spool = tmp_path / "spool"

    report = migrate(spool_root=spool, roots={"opencode": db}, providers=["opencode"])

    opencode = report["by_provider"]["opencode"]
    assert opencode["status"] == "bounded_limit_required"
    assert opencode["found"] == 1
    assert opencode["spooled"] == 0
    assert not spool.exists() or not list((spool / "pending").glob("*.json"))


def test_migrate_opencode_root_unavailable(tmp_path):
    report = migrate(
        spool_root=tmp_path / "s",
        roots={"opencode": tmp_path / "missing.db"},
        providers=["opencode"],
        dry_run=True,
    )
    assert report["by_provider"]["opencode"]["status"] == "root_unavailable"
    assert report["spooled"] == 0


def test_build_opencode_migration_request(tmp_path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    req = build_opencode_migration_request(db, OPENCODE_SESSION_ID, project=PROJECT)
    assert req["provider"] == "opencode"
    assert req["session_id"] == OPENCODE_SESSION_ID
    assert req["source_locator"]["runtime_handle"] == str(db)


# --- CLI ------------------------------------------------------------------


def test_cli_transcript_capture_opencode_spools_without_leaking_path(tmp_path, monkeypatch, capsys):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    payload = _opencode_session_payload(str(db))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert (
        main(
            [
                "transcript-capture",
                "--provider",
                "opencode",
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
    assert output["provider"] == "opencode"
    assert output["source_locator_hash"].startswith("sha256:")
    assert str(db) not in output_text
    assert OPENCODE_SESSION_ID not in output_text
    pending = next((tmp_path / "capture-spool" / "pending").glob("*.json"))
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
