from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace
import urllib.error

from dendrite.redaction import redact_public_ingress_text, redact_text_v2
from dendrite.transcript_ingest import IngressQueueClient


def test_ingress_queue_client_posts_versioned_redacted_contract(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"accepted":true,"jobId":"job_http_001","status":"queued"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["method"] = request.method
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    body = "---\nschema_version: agent_knowledge_document.v2\n---\n## Turn\n\n- redacted body\n"
    metadata = {
        "schema_version": "agent_knowledge_document.v2",
        "result_type": "conversation_chunk",
        "knowledge_id": "kn_123",
        "provider": "claude",
        "project": "dendrite",
        "turn_start_index": 1,
        "turn_end_index": 2,
    }
    content_hash = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    response = IngressQueueClient(base_url="http://127.0.0.1:8080").enqueue_document(
        source={
            "host": "mac_mini",
            "producer": "session-compactor",
            "provider": "claude",
            "project": "dendrite",
        },
        packed=SimpleNamespace(filename="chunk.md", body=body, metadata=metadata),
        content_hash=content_hash,
        target_profile="index-transcript-memory",
        kind="conversation_chunk",
        idempotency_key=f"claude:conversation_chunk:{content_hash}",
    )

    assert response == {"job_id": "job_http_001", "status": "queued"}
    assert captured["url"] == "http://127.0.0.1:8080/v1/ingest/enqueue"
    assert captured["timeout"] == 10.0
    assert captured["method"] == "POST"
    assert captured["headers"]["Content-type"] == "application/json"
    payload = captured["payload"]
    assert payload["schemaVersion"] == "rag_ingress_enqueue.v1"
    assert payload["source"] == {
        "host": "mac_mini",
        "producer": "session-compactor",
        "provider": "claude",
        "project": "dendrite",
    }
    assert payload["payload"]["kind"] == "redacted_rag_ready_document"
    assert payload["payload"]["redactionVersion"] == "redaction.v2"
    assert payload["payload"]["document"]["contentType"] == "text/markdown"
    assert payload["payload"]["document"]["body"] == body
    assert payload["contentHash"] == content_hash
    assert payload["targetProfile"] == "index-transcript-memory"
    assert payload["kind"] == "conversation_chunk"
    assert payload["payload"]["document"]["metadata"]["turn_start_index"] == "1"
    assert payload["payload"]["document"]["metadata"]["turn_end_index"] == "2"


def test_ingress_queue_client_rejects_credentials_in_base_url() -> None:
    try:
        IngressQueueClient(base_url="http://user:pass@127.0.0.1:8080")
    except ValueError as exc:
        assert "must not contain credentials" in str(exc)
    else:
        raise AssertionError("base_url credentials must be rejected")


def test_ingress_queue_client_treats_http5xx_as_retryable(monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        _ = timeout
        raise urllib.error.HTTPError("http://127.0.0.1:8080", 503, "unavailable", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    try:
        IngressQueueClient(base_url="http://127.0.0.1:8080").enqueue_document(
            source={"producer": "test"},
            packed=SimpleNamespace(filename="chunk.md", body="# body\n", metadata={}),
            content_hash="sha256:" + "a" * 64,
            target_profile="index-transcript-memory",
            kind="conversation_chunk",
            idempotency_key="codex:conversation_chunk:test",
        )
    except RuntimeError as exc:
        assert "unreachable" in str(exc)
        assert "http_503" in str(exc)
    else:
        raise AssertionError("HTTP 5xx must be retryable")


def test_ingress_queue_client_invalid_json_response_is_retryable(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: FakeResponse())

    try:
        IngressQueueClient(base_url="http://127.0.0.1:8080").enqueue_document(
            source={"producer": "test"},
            packed=SimpleNamespace(filename="chunk.md", body="# body\n", metadata={}),
            content_hash="sha256:" + "a" * 64,
            target_profile="index-transcript-memory",
            kind="conversation_chunk",
            idempotency_key="codex:conversation_chunk:test",
        )
    except RuntimeError as exc:
        assert "invalid_json" in str(exc)
    else:
        raise AssertionError("invalid ingress responses must be retryable")


def test_ingress_queue_client_keeps_server_rejection_reason(monkeypatch) -> None:
    """A 4xx must say *why*: status + safe exception class, never payload text."""

    def fake_urlopen(_request, timeout):
        _ = timeout
        body = io.BytesIO(
            json.dumps(
                {"accepted": False, "status": "rejected", "error": "SecretLikeMetadataError"}
            ).encode("utf-8")
        )
        raise urllib.error.HTTPError("http://127.0.0.1:8080/v1/ingest/enqueue", 400, "bad request", {}, body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    try:
        IngressQueueClient(base_url="http://127.0.0.1:8080").enqueue_document(
            source={"producer": "test"},
            packed=SimpleNamespace(filename="chunk.md", body="# body\n", metadata={}),
            content_hash="sha256:" + "a" * 64,
            target_profile="index-transcript-memory",
            kind="conversation_chunk",
            idempotency_key="codex:conversation_chunk:test",
        )
    except RuntimeError as exc:
        assert "status=rejected" in str(exc)
        assert "error=SecretLikeMetadataError" in str(exc)
    else:
        raise AssertionError("HTTP 400 must surface as a rejection")


def test_public_redaction_output_is_a_conservative_fixed_point() -> None:
    """The wire body must satisfy ``redact_text_v2(body) == body``.

    The worker fail-closes with ``quarantined_wire_redaction`` otherwise. Public
    term substitutions ("token" -> "credential") used to synthesize
    credential-shaped text that the conservative pass would rewrite again.
    """
    raw = (
        "---\nschema_version: agent_knowledge_document.v2\nresult_type: conversation_chunk\n---\n"
        "Basic token handling and access token rotation were discussed.\n"
        "postgresql://alice:hunter2xyz@db.internal:5432/app kept working.\n"
        "Bearer token refresh and raw_transcript piping came up.\n"
    )
    public = redact_public_ingress_text(raw)
    assert redact_text_v2(public) == public
    assert redact_public_ingress_text(public) == public
    assert "hunter2xyz" not in public
    assert "alice" not in public
