from __future__ import annotations

import json
import hashlib
import fcntl
import re
import sqlite3
from dataclasses import asdict

import pytest

from dendrite.redaction import redact_public_ingress_text
from dendrite.transcript_capture import TranscriptCaptureSpool, normalize_provider_capture_request
from dendrite.transcript_drain import build_drain_document, build_drain_documents, drain_transcript_spool_once
from dendrite.transcript_source import MAX_TRANSCRIPT_BODY_CHARS, adapter_for
from dendrite.transcript_ingest import IngressQueueClient


def _request(source, provider="claude"):
    return normalize_provider_capture_request(
        provider,
        {
            "hook_event_name": "Stop",
            "session_id": "multipart-session",
            "transcript_path": str(source),
            "cwd": "/Users/example/Projects/example",
        },
        project="example",
    )


def _text(part):
    return part.body.split("## Transcript\n\n", 1)[1]


class RecordingIngress:
    def __init__(self, fail_call=None):
        self.calls = []
        self.accepted = []
        self.fail_call = fail_call

    def enqueue_document(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == self.fail_call:
            raise RuntimeError("ingress enqueue failed: unreachable")
        self.accepted.append(kwargs)
        return {"status": "queued"}


@pytest.mark.parametrize(
    "text",
    [
        "single very long line " * 30_000 + "LATEST-END",
        ("한글🙂\t\"\\\x01\n" * 35_000) + "마지막 내용\n\n  ",
        ("line\n\n" * 80_000) + "tail without newline",
        "",
        "\n \r\n \t",
    ],
)
def test_parts_preserve_all_redacted_text_and_positions(tmp_path, text):
    source = tmp_path / "session.jsonl"
    source.write_text(text, encoding="utf-8", newline="")
    request = _request(source)
    parts = build_drain_documents(request)
    # Text-mode source reading normalizes CRLF; partitioning must add no further changes.
    expected = redact_public_ingress_text(source.read_text(encoding="utf-8"))
    assert "".join(_text(p) for p in parts) == expected
    assert len({p.metadata["chunk_id"] for p in parts}) == len(parts)
    assert len({p.filename for p in parts}) == len(parts)
    assert len({p.metadata["capture_snapshot_hash"] for p in parts}) == 1
    cursor = 0
    for index, part in enumerate(parts, 1):
        metadata = part.metadata
        assert metadata["part_index"] == index
        assert metadata["part_count"] == len(parts)
        assert metadata["char_start"] == cursor
        cursor = metadata["char_end"]
        assert _text(part) == expected[metadata["char_start"]:cursor]
        assert metadata["turn_start_index"] <= metadata["turn_end_index"]
        assert len(metadata["chunk_id"]) <= 64
        assert len(json.dumps(part.body, ensure_ascii=True)) <= MAX_TRANSCRIPT_BODY_CHARS
        assert "[truncated]" not in part.body
    assert cursor == len(expected)
    assert [asdict(p) for p in build_drain_documents(request)] == [asdict(p) for p in parts]
    if len(parts) > 1:
        with pytest.raises(ValueError, match="transcript_requires_multiple_parts"):
            build_drain_document(request)
    else:
        assert build_drain_document(request) == parts[0]


def test_redaction_happens_before_any_partition(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text(
        "x" * (MAX_TRANSCRIPT_BODY_CHARS - 2000)
        + " TOKEN_VALUE=never-ship-this-secret /Users/example/private/file.txt\n"
        + "tail " * 45_000,
        encoding="utf-8",
    )
    parts = build_drain_documents(_request(source))
    assert len(parts) > 1
    text = "".join(_text(p) for p in parts)
    assert text == redact_public_ingress_text(source.read_text())
    assert "never-ship-this-secret" not in text
    assert "/Users/example" not in text
    assert "never-ship-this-secret" not in json.dumps([asdict(p) for p in parts])

@pytest.mark.parametrize("prefix", ["secret ", "password\n", 'api_key":"', "access-token: "])
def test_long_spaced_credential_values_are_redacted_before_delivery(tmp_path, prefix):
    source = tmp_path / "session.jsonl"
    value = "synthetic-value-for-redaction-test-123456"
    source.write_text("x" * 200_000 + "\n" + prefix + value + "\nLATEST")
    parts = build_drain_documents(_request(source))
    serialized = json.dumps([asdict(p) for p in parts])
    assert value not in serialized
    expected = redact_public_ingress_text(source.read_text())
    assert "".join(_text(p) for p in parts) == expected
    guard = re.compile(
        r"""\b(?:access[_-]?token|api[_-]?key|token|secret|password|passwd)\b["':=\s]{1,4}[A-Za-z0-9._/+\-]{20,}""",
        re.I,
    )
    assert all(not guard.search(p.body) for p in parts)

def test_multipart_uses_existing_http_contract_with_bounded_unicode_payloads(tmp_path, monkeypatch):
    source = tmp_path / "session.jsonl"
    original = "한글🙂\n" * 35_000 + "마지막"
    source.write_text(original)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    payloads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"accepted":true,"status":"queued"}'

    def urlopen(request, timeout):
        # Serialized body stays within 180k, plus the small existing ingress envelope.
        assert len(request.data) < 200_000
        payloads.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    ingress = IngressQueueClient(base_url="http://127.0.0.1:18080")
    for _ in range(10):
        report = drain_transcript_spool_once(
            capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=2
        )
        if report["queued_count"]:
            break
    assert spool.depth_counts()["acked"] == 1
    assert len(payloads) > 2
    reconstructed = []
    for index, payload in enumerate(payloads, 1):
        assert payload["schemaVersion"] == "rag_ingress_enqueue.v1"
        assert payload["payload"]["kind"] == "redacted_rag_ready_document"
        document = payload["payload"]["document"]
        metadata = document["metadata"]
        assert metadata["part_index"] == str(index)
        assert metadata["part_count"] == str(len(payloads))
        assert payload["contentHash"] == "sha256:" + hashlib.sha256(document["body"].encode()).hexdigest()
        reconstructed.append(document["body"].split("## Transcript\n\n", 1)[1])
    assert "".join(reconstructed) == original


def test_post_budget_defers_capture_ack_and_survives_source_removal(tmp_path):
    source = tmp_path / "session.jsonl"
    original = "conversation\n" * 70_000 + "LATEST-END\n\n"
    source.write_text(original)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    request = _request(source)
    spool.enqueue(request)
    ingress = RecordingIngress()
    first = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=2
    )
    assert len(ingress.calls) == 2
    assert first["queued_part_count"] == 2
    assert first["queued_count"] == 0
    assert first["delivery_pending_count"] == 1
    assert first["status"] == "queued"
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}
    pending = next((spool.root / "pending").glob("*.json"))
    progress = json.loads(pending.read_text())
    assert progress["content_policy"] == "locator_only"
    assert progress["delivery"]["next_part"] == 2
    assert "LATEST-END" not in pending.read_text()
    snapshots = list((spool.root / "outbox/snapshots").glob("*.json"))
    assert len(snapshots) == 1
    assert snapshots[0].stat().st_mode & 0o777 == 0o600
    assert snapshots[0].parent.stat().st_mode & 0o777 == 0o700
    source.unlink()  # Resume must use the immutable snapshot, not a changed/missing source.
    completed = False
    for _ in range(10):
        before = len(ingress.calls)
        report = drain_transcript_spool_once(
            capture_spool=TranscriptCaptureSpool(spool.root), ingress=ingress,
            target_profile="index-transcript-memory", max_items=2,
        )
        assert len(ingress.calls) - before <= 2
        if report["queued_count"]:
            completed = True
            break
    assert completed
    assert spool.depth_counts() == {"pending": 0, "processing": 0, "acked": 1, "quarantine": 0}
    assert "".join(_text(x["packed"]) for x in ingress.accepted) == original
    assert len({x["idempotency_key"] for x in ingress.calls}) == len(ingress.calls)
    assert not list((spool.root / "outbox/snapshots").glob("*.json"))


def test_midpart_failure_resumes_without_resending_accepted_prefix(tmp_path):
    source = tmp_path / "session.jsonl"
    original = "old conversation " * 40_000 + "original tail"
    source.write_text(original)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress(fail_call=2)
    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=5
    )
    assert report["status"] == "retry_pending"
    assert report["queued_part_count"] == 1
    assert report["queued_count"] == 0
    pending = next((spool.root / "pending").glob("*.json"))
    assert json.loads(pending.read_text())["delivery"]["next_part"] == 1
    failed = ingress.calls[-1]
    source.write_text("completely changed conversation")
    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=5
    )
    assert report["queued_count"] == 1
    assert ingress.calls[2]["idempotency_key"] == failed["idempotency_key"]
    assert ingress.calls[2]["packed"] == failed["packed"]
    assert "".join(_text(x["packed"]) for x in ingress.accepted) == original
    assert len({x["idempotency_key"] for x in ingress.accepted}) == len(ingress.accepted)


def test_lost_acceptance_receipt_replays_identical_part(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("message\n" * 40_000)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress(fail_call=1)
    first = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert first["status"] == "retry_pending"
    source.write_text("new content after a possibly accepted POST")
    second = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert second["queued_count"] == 1
    assert ingress.calls[0]["packed"] == ingress.calls[1]["packed"]
    assert ingress.calls[0]["idempotency_key"] == ingress.calls[1]["idempotency_key"]


def test_checkpoint_failure_replays_the_same_accepted_part(tmp_path, monkeypatch):
    source = tmp_path / "session.jsonl"
    source.write_text("message\n" * 40_000)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress()
    checkpoint = spool.checkpoint_delivery
    failed = False

    def fail_checkpoint(path, *, snapshot_hash, next_part):
        nonlocal failed
        if next_part == 1 and not failed:
            failed = True
            raise OSError("synthetic disk error")
        return checkpoint(path, snapshot_hash=snapshot_hash, next_part=next_part)

    monkeypatch.setattr(spool, "checkpoint_delivery", fail_checkpoint)
    first = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert first["status"] == "retry_pending"
    assert first["last_error_class"] == "delivery_state_unavailable"
    source.write_text("changed source")
    second = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert second["queued_count"] == 1
    assert ingress.calls[0]["idempotency_key"] == ingress.calls[1]["idempotency_key"]
    assert ingress.calls[0]["packed"] == ingress.calls[1]["packed"]


def test_ack_failure_resumes_without_another_post(tmp_path, monkeypatch):
    source = tmp_path / "session.jsonl"
    source.write_text("message\n")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress()
    ack = spool.ack

    def fail_ack(path):
        raise OSError("synthetic disk error")

    monkeypatch.setattr(spool, "ack", fail_ack)
    first = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert first["status"] == "retry_pending"
    assert first["queued_part_count"] == 1
    monkeypatch.setattr(spool, "ack", ack)
    second = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert second["queued_count"] == 1
    assert second["queued_part_count"] == 0
    assert len(ingress.calls) == 1


def test_new_capture_of_grown_source_has_distinct_chunk_ids(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("original\n" * 30_000)
    old_request = _request(source)
    old_parts = build_drain_documents(old_request)
    source.write_text(source.read_text() + "new tail\n" * 20_000)
    new_request = _request(source)
    new_parts = build_drain_documents(new_request)
    assert new_request["request_id"] != old_request["request_id"]
    assert {p.metadata["chunk_id"] for p in old_parts}.isdisjoint(
        p.metadata["chunk_id"] for p in new_parts
    )
    assert "".join(_text(p) for p in new_parts) == source.read_text()


@pytest.mark.parametrize("corruption", ["missing_snapshot", "modified_snapshot", "invalid_progress", "changed_target"])
def test_bad_resume_state_is_not_acknowledged_or_sent(tmp_path, corruption):
    source = tmp_path / "session.jsonl"
    source.write_text("message\n" * 70_000)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress()
    drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=1
    )
    snapshot = next((spool.root / "outbox/snapshots").glob("*.json"))
    pending = next((spool.root / "pending").glob("*.json"))
    if corruption == "missing_snapshot":
        snapshot.unlink()
    elif corruption == "modified_snapshot":
        snapshot.write_text("{}")
    elif corruption == "invalid_progress":
        data = json.loads(pending.read_text())
        data["delivery"]["next_part"] = 999_999
        pending.write_text(json.dumps(data))
    before = len(ingress.calls)
    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress,
        target_profile="index-other-memory" if corruption == "changed_target" else "index-transcript-memory",
    )
    assert report["status"] == "quarantined"
    assert len(ingress.calls) == before
    assert spool.depth_counts()["acked"] == 0


def test_hermes_source_is_not_truncated_before_partitioning(tmp_path):
    db = tmp_path / "state.db"
    text = "Hermes long message " * 30_000 + "TAIL"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp TEXT)")
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?)", ("multipart-session", "user", text, "2026-10-21"))
    before = db.read_bytes()
    request = _request(db, provider="hermes")
    expected = adapter_for("hermes").read_redacted_transcript(request)
    assert text in expected
    parts = build_drain_documents(request)
    assert len(parts) > 1
    assert "".join(_text(p) for p in parts) == expected
    assert db.read_bytes() == before


def test_zero_budget_does_not_prepare_or_send_a_snapshot(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("message\n" * 40_000)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress()
    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=0
    )
    assert report["attempted_count"] == 0
    assert not ingress.calls
    assert not (spool.root / "outbox").exists()


def test_interrupted_processing_is_recovered_under_the_drain_lock(tmp_path):
    source = tmp_path / "session.jsonl"
    original = "message\n" * 40_000
    source.write_text(original)
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    ingress = RecordingIngress()
    drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory", max_items=1
    )
    spool.claim_next()  # Simulate a process dying after claiming the partial capture.
    source.unlink()
    report = drain_transcript_spool_once(
        capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
    )
    assert report["recovered_processing_count"] == 1
    assert report["queued_count"] == 1
    assert "".join(_text(x["packed"]) for x in ingress.accepted) == original
    assert len({x["idempotency_key"] for x in ingress.calls}) == len(ingress.calls)


def test_second_drain_cannot_take_an_active_consumers_processing_entry(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("message")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_request(source))
    spool.claim_next()
    ingress = RecordingIngress()
    with (spool.root / ".transcript-drain.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = drain_transcript_spool_once(
            capture_spool=spool, ingress=ingress, target_profile="index-transcript-memory"
        )
    assert report["last_status"] == "already_running"
    assert report["recovered_processing_count"] == 0
    assert not ingress.calls
    assert spool.depth_counts()["processing"] == 1
