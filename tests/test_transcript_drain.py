from __future__ import annotations

import json
import urllib.error

from dendrite.cli import main
from dendrite.transcript_capture import TranscriptCaptureSpool, normalize_provider_capture_request


def _capture_request(source, *, project: str = "neurons") -> dict:
    return normalize_provider_capture_request(
        "codex",
        {
            "hook_event_name": "Stop",
            "session_id": "codex-session-123",
            "transcript_path": str(source),
            "cwd": f"/Users/ddalkak/Projects/{project}",
        },
        project="dendrite",
    )


def test_transcript_drain_enqueues_redacted_document_and_acks_spool(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text(
        json.dumps(
            {
                "role": "user",
                "content": "work on neurons TOKEN_VALUE=secret-123 at /Users/example/private/file.txt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"accepted":true,"jobId":"job_001","status":"queued"}'

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
            "--target-profile",
            "ragflow-transcript-memory",
            "--max-items",
            "5",
        ]
    )

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "queued"
    assert report["queued_count"] == 1
    assert report["network_used"] is True
    assert report["raw_locator_printed"] is False
    assert report["raw_transcript_printed"] is False
    assert spool.depth_counts() == {"pending": 0, "processing": 0, "acked": 1, "quarantine": 0}

    payload = captured["payload"]
    document = payload["payload"]["document"]
    serialized = json.dumps(document, sort_keys=True)
    assert payload["source"]["producer"] == "dendrite-transcript-drain"
    assert payload["source"]["project"] == "neurons"
    assert payload["targetProfile"] == "ragflow-transcript-memory"
    assert payload["kind"] == "conversation_chunk"
    assert document["metadata"]["project"] == "neurons"
    assert document["metadata"]["agent_id"] == "codex-transcript-capture"
    assert "secret-123" not in serialized
    assert "/Users/example/private/file.txt" not in serialized
    assert str(source) not in serialized


def test_transcript_drain_requeues_recoverable_ingress_failure(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    def fake_urlopen(_request, timeout=None):
        _ = timeout
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "retry_pending"
    assert report["last_error_class"] == "ingress_unreachable"
    assert report["network_used"] is True
    assert report["retry_pending_count"] == 1
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}
    pending = next((spool.root / "pending").glob("*.json"))
    payload = json.loads(pending.read_text(encoding="utf-8"))
    assert payload["last_failure"]["recoverable"] is True
    assert payload["last_failure"]["retry_attempts"] == 1
    assert payload["last_failure"]["last_attempt_at"]

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    _ = json.loads(capsys.readouterr().out)
    pending = next((spool.root / "pending").glob("*.json"))
    payload = json.loads(pending.read_text(encoding="utf-8"))
    assert payload["last_failure"]["retry_attempts"] == 2


def test_transcript_drain_requeues_retryable_http5xx(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    def fake_urlopen(_request, timeout=None):
        _ = timeout
        raise urllib.error.HTTPError("http://127.0.0.1:18080", 503, "unavailable", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "retry_pending"
    assert report["last_error_class"] == "ingress_unreachable"
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}


def test_transcript_drain_requeues_unsafe_http4xx(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    def fake_urlopen(_request, timeout=None):
        _ = timeout
        raise urllib.error.HTTPError("http://127.0.0.1:18080", 403, "forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "retry_pending"
    assert report["last_error_class"] == "ingress_unreachable"
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}


def test_transcript_drain_requeues_invalid_ingress_response(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout=None: FakeResponse())

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "retry_pending"
    assert report["last_error_class"] == "ingress_invalid_json"
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}


def test_transcript_drain_requeues_unparseable_ingress_response(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"\xff"

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout=None: FakeResponse())

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "retry_pending"
    assert report["last_error_class"] == "ingress_unreachable"
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}


def test_transcript_drain_requeues_transient_accepted_false(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"accepted":false,"status":"backpressure"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout=None: FakeResponse())

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "retry_pending"
    assert report["last_error_class"] == "ingress_unreachable"
    assert spool.depth_counts() == {"pending": 1, "processing": 0, "acked": 0, "quarantine": 0}


def test_transcript_drain_quarantines_safe_http4xx_rejection(tmp_path, monkeypatch, capsys):
    source = tmp_path / "codex-session.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    error = urllib.error.HTTPError(
        "http://127.0.0.1:18080", 400, "bad request", {}, _BytesResponse(b'{"status":"payload_invalid"}')
    )

    def fake_urlopen(_request, timeout=None):
        _ = timeout
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "quarantined"
    assert report["last_error_class"] == "ingress_rejected"
    assert spool.depth_counts() == {"pending": 0, "processing": 0, "acked": 0, "quarantine": 1}


def test_transcript_drain_quarantines_missing_source_without_raw_path(tmp_path, capsys):
    source = tmp_path / "missing.jsonl"
    spool = TranscriptCaptureSpool(tmp_path / "spool")
    spool.enqueue(_capture_request(source))

    rc = main(
        [
            "transcript-drain",
            "--once",
            "--capture-spool",
            str(spool.root),
            "--ingress-url",
            "http://127.0.0.1:18080",
        ]
    )

    assert rc == 1
    output = capsys.readouterr().out
    assert str(source) not in output
    report = json.loads(output)
    assert report["status"] == "quarantined"
    assert report["last_error_class"] == "source_unreadable"
    assert report["network_used"] is False
    assert spool.depth_counts() == {"pending": 0, "processing": 0, "acked": 0, "quarantine": 1}


class _BytesResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None
