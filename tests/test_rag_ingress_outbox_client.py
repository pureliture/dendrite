from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from threading import Thread
import urllib.error

from dendrite.rag_ingress.outbox_client import (
    FileBackedIngressOutbox,
    IngressEnqueueRejected,
    IngressEnqueueUnreachable,
    RagIngressHttpClient,
    validate_outbox_payload,
)
from dendrite.rag_ingress.rag_ready_document import build_ingress_enqueue_payload, build_rag_ready_document


def _payload():
    document = build_rag_ready_document(
        target_profile="transcript-memory",
        document_kind="conversation_chunk",
        source_namespace="codex",
        source_alias="dendrite/session",
        privacy_class="private",
        body="# Outbox\n\nbounded client outbox document",
        filename="outbox.md",
        metadata={"project": "dendrite", "privacy_class": "private"},
    )
    return build_ingress_enqueue_payload(
        document,
        source={"provider": "codex", "source_alias": "dendrite/session"},
    )


def test_outbox_idempotently_persists_redacted_payload(tmp_path):
    outbox = FileBackedIngressOutbox(tmp_path / "outbox")
    first = outbox.enqueue(_payload())
    second = outbox.enqueue(_payload())

    assert first.item_id == second.item_id
    assert first.status == "pending"
    assert second.status == "pending"
    assert outbox.depth_counts() == {"pending": 1, "acked": 0, "quarantine": 0}


def test_outbox_rejects_secret_like_metadata(tmp_path):
    payload = _payload()
    payload["payload"]["document"]["metadata"]["api_token"] = "secret"

    try:
        FileBackedIngressOutbox(tmp_path / "outbox").enqueue(payload)
    except ValueError as exc:
        assert "secret-like metadata key" in str(exc)
    else:
        raise AssertionError("secret-like metadata must be rejected")


class RejectingClient:
    def enqueue_document_payload(self, _payload):
        raise IngressEnqueueRejected("rejected")


class UnreachableClient:
    def enqueue_document_payload(self, _payload):
        raise IngressEnqueueUnreachable("unreachable")


def test_outbox_flush_quarantines_rejected_payload(tmp_path):
    outbox = FileBackedIngressOutbox(tmp_path / "outbox")
    outbox.enqueue(_payload())

    report = outbox.flush(RejectingClient())

    assert report["sent"] == 0
    assert report["quarantined"] == 1
    assert report["pending"] == 0
    assert report["quarantine"] == 1


def test_outbox_flush_keeps_pending_when_server_unreachable(tmp_path):
    outbox = FileBackedIngressOutbox(tmp_path / "outbox")
    outbox.enqueue(_payload())

    report = outbox.flush(UnreachableClient())

    assert report["stopped_unreachable"] is True
    assert report["pending"] == 1
    assert report["acked"] == 0
    assert report["quarantine"] == 0


def test_http_client_flushes_to_ingress_runtime(tmp_path):
    import socketserver

    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            captured.append(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"accepted":true,"jobId":"job_test","status":"queued"}')

        def log_message(self, _format, *_args):
            return

    class TestServer(socketserver.TCPServer):
        allow_reuse_address = True

    server = TestServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    outbox = FileBackedIngressOutbox(tmp_path / "outbox")
    outbox.enqueue(_payload())
    client = RagIngressHttpClient(base_url=f"http://127.0.0.1:{server.server_address[1]}")
    try:
        report = outbox.flush(client)
    finally:
        server.shutdown()
        server.server_close()

    assert report["sent"] == 1
    assert report["acked"] == 1
    assert captured[0]["schemaVersion"] == "rag_ingress_enqueue.v1"


def test_http_client_rejects_credentials_in_base_url():
    try:
        RagIngressHttpClient(base_url="http://user:pass@127.0.0.1:8080")
    except ValueError as exc:
        assert "must not contain credentials" in str(exc)
    else:
        raise AssertionError("base_url credentials must be rejected")


def test_http_client_treats_5xx_as_unreachable(monkeypatch):
    def fake_urlopen(_request, timeout):
        _ = timeout
        raise urllib.error.HTTPError("http://127.0.0.1:18080", 503, "unavailable", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueUnreachable as exc:
        assert "unreachable" in str(exc)
        assert "http_503" in str(exc)
    else:
        raise AssertionError("HTTP 5xx must be retryable/unreachable")


def test_http_client_treats_unsafe_4xx_as_unreachable(monkeypatch):
    def fake_urlopen(_request, timeout):
        _ = timeout
        raise urllib.error.HTTPError("http://127.0.0.1:18080", 403, "forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueUnreachable as exc:
        assert "unreachable" in str(exc)
        assert "http_403" in str(exc)
    else:
        raise AssertionError("unsafe HTTP 4xx must be retryable/unreachable")


def test_http_client_treats_safe_4xx_payload_rejection_as_rejected(monkeypatch):
    error = urllib.error.HTTPError(
        "http://127.0.0.1:18080", 400, "bad request", {}, _BytesResponse(b'{"status":"payload_invalid"}')
    )

    def fake_urlopen(_request, timeout):
        _ = timeout
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueRejected as exc:
        assert "payload_invalid" in str(exc)
    else:
        raise AssertionError("safe HTTP 4xx payload rejection must be rejected")


def test_http_client_treats_unsafe_4xx_with_safe_body_as_unreachable(monkeypatch):
    error = urllib.error.HTTPError(
        "http://127.0.0.1:18080", 403, "forbidden", {}, _BytesResponse(b'{"status":"payload_invalid"}')
    )

    def fake_urlopen(_request, timeout):
        _ = timeout
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueUnreachable as exc:
        assert "http_403" in str(exc)
    else:
        raise AssertionError("unsafe HTTP 4xx must stay retryable even with a safe-looking body")


def test_http_client_treats_invalid_json_response_as_unreachable(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: FakeResponse())

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueUnreachable as exc:
        assert "invalid_json" in str(exc)
    else:
        raise AssertionError("invalid ingress responses must be retryable/unreachable")


def test_http_client_treats_invalid_utf8_response_as_unreachable(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"\xff"

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: FakeResponse())

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueUnreachable as exc:
        assert "invalid_response" in str(exc)
    else:
        raise AssertionError("unparseable ingress responses must be retryable/unreachable")


def test_http_client_treats_transient_accepted_false_as_unreachable(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"accepted":false,"status":"backpressure"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: FakeResponse())

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueUnreachable as exc:
        assert "backpressure" in str(exc)
    else:
        raise AssertionError("transient accepted=false must be retryable/unreachable")


def test_http_client_treats_safe_accepted_false_payload_rejection_as_rejected(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"accepted":false,"status":"payload_invalid"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: FakeResponse())

    try:
        RagIngressHttpClient(base_url="http://127.0.0.1:18080").enqueue_document_payload(_payload())
    except IngressEnqueueRejected as exc:
        assert "payload_invalid" in str(exc)
    else:
        raise AssertionError("safe payload rejection must be permanent/rejected")


def test_validate_outbox_payload_rejects_invalid_body():
    payload = _payload()
    payload["payload"]["document"]["body"] = ""

    try:
        validate_outbox_payload(payload)
    except ValueError as exc:
        assert "document.body" in str(exc)
    else:
        raise AssertionError("empty document body must be rejected")


class _BytesResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None
