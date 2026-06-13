from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import replace

from .rag_ingress.rag_ready_document import RagReadyDocument, build_rag_ready_document
from .redaction import redact_public_ingress_text, redact_text_v2


INGRESS_SCHEMA_VERSION = "rag_ingress_enqueue.v1"
INGRESS_PAYLOAD_KIND = "redacted_rag_ready_document"
DEFAULT_TRANSCRIPT_TARGET_PROFILE = "ragflow-transcript-memory"


class IngressQueueClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0):
        if not base_url:
            raise ValueError("ingress base_url is required")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def enqueue_document(
        self,
        *,
        source,
        packed,
        content_hash: str,
        target_profile: str,
        kind: str,
        idempotency_key: str,
    ) -> dict:
        request_body = build_ingress_enqueue_request_body(
            source=source,
            packed=packed,
            content_hash=content_hash,
            target_profile=target_profile,
            kind=kind,
            idempotency_key=idempotency_key,
        )
        data = json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/ingest/enqueue",
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ingress enqueue rejected: http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("ingress enqueue failed: unreachable") from exc
        try:
            payload = json.loads(response_body or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("ingress enqueue failed: invalid_json") from exc
        if payload.get("accepted") is not True:
            status = str(payload.get("status") or "rejected")
            raise RuntimeError(f"ingress enqueue rejected: {status}")
        return {
            "job_id": str(payload.get("jobId") or payload.get("job_id") or ""),
            "status": str(payload.get("status") or "queued"),
        }


def build_ingress_enqueue_request_body(
    *,
    source,
    packed,
    content_hash: str,
    target_profile: str,
    kind: str,
    idempotency_key: str,
) -> dict:
    return {
        "schemaVersion": INGRESS_SCHEMA_VERSION,
        "source": dict(source),
        "payload": {
            "kind": INGRESS_PAYLOAD_KIND,
            "redactionVersion": "redaction.v2",
            "document": {
                "filename": packed.filename,
                "contentType": "text/markdown",
                "body": packed.body,
                "metadata": _string_metadata(packed.metadata),
            },
        },
        "contentHash": content_hash,
        "targetProfile": target_profile,
        "kind": _queue_transport_kind(kind),
        "idempotencyKey": idempotency_key,
    }


def _queue_transport_kind(kind: str) -> str:
    return kind


def build_transcript_rag_ready_document(
    *,
    packed,
    source_namespace: str,
    target_profile: str,
    source_alias: str,
    privacy_class: str = "private",
) -> RagReadyDocument:
    return build_rag_ready_document(
        target_profile=target_profile,
        document_kind=packed.kind,
        source_namespace=source_namespace,
        source_alias=source_alias,
        privacy_class=privacy_class,
        body=packed.body,
        filename=packed.filename,
        metadata=dict(packed.metadata),
    )


def _public_ingress_packed_document(packed):
    return replace(
        packed,
        title=redact_public_ingress_text(str(packed.title)),
        body=redact_public_ingress_text(str(packed.body)),
        metadata={str(key): _public_ingress_metadata_value(value) for key, value in packed.metadata.items()},
        filename=redact_public_ingress_text(str(packed.filename)),
    )


def _conservative_ingress_packed_document(packed):
    return replace(
        packed,
        title=redact_text_v2(str(packed.title)),
        body=redact_text_v2(str(packed.body)),
        metadata={str(key): _conservative_metadata_value(value) for key, value in packed.metadata.items()},
        filename=redact_text_v2(str(packed.filename)),
    )


def _conservative_metadata_value(value):
    if isinstance(value, str):
        return redact_text_v2(value)
    if isinstance(value, dict):
        return {str(key): _conservative_metadata_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_conservative_metadata_value(item) for item in value]
    return value


def _public_ingress_metadata_value(value):
    if isinstance(value, str):
        return redact_public_ingress_text(value)
    if isinstance(value, dict):
        return {str(key): _public_ingress_metadata_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_public_ingress_metadata_value(item) for item in value]
    return value


def _idempotency_key(provider: str, kind: str, content_hash: str) -> str:
    return f"{provider}:{kind}:{content_hash}"


def _string_metadata(metadata: dict) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}


def _classify_error(exc: Exception) -> str:
    message = str(exc)
    if "source_unreadable" in message:
        return "source_unreadable"
    if "source_unproven" in message:
        return "source_unproven"
    if "source_parse_failed" in message:
        return "source_parse_failed"
    if "source_policy_blocked" in message:
        return "source_policy_blocked"
    if "ingress enqueue" in message:
        if "unreachable" in message:
            return "ingress_unreachable"
        if "invalid_json" in message:
            return "ingress_invalid_json"
        match = re.search(r"http_(\d{3})", message)
        if match:
            return f"ingress_rejected_http_{match.group(1)}"
        return "ingress_rejected"
    return exc.__class__.__name__


def _redacted_error_message(exc: Exception) -> str:
    error_class = _classify_error(exc)
    if error_class.startswith("source_") or error_class.startswith("ingress_"):
        return error_class
    return "transcript ingest failed"


def _is_recoverable_ingest_error(exc: Exception) -> bool:
    return _classify_error(exc) not in {
        "source_parse_failed",
        "source_policy_blocked",
        "source_unproven",
        "source_unreadable",
    }


def _quarantine_failure_record(exc: Exception) -> dict:
    return {
        "error_class": _classify_error(exc),
        "message": _redacted_error_message(exc),
        "recoverable": _is_recoverable_ingest_error(exc),
    }
