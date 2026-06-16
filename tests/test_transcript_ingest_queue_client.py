from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

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
        target_profile="ragflow-transcript-memory",
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
    assert payload["targetProfile"] == "ragflow-transcript-memory"
    assert payload["kind"] == "conversation_chunk"
    assert payload["payload"]["document"]["metadata"]["turn_start_index"] == "1"
    assert payload["payload"]["document"]["metadata"]["turn_end_index"] == "2"
