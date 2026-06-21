from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ..ingress_transport import (
    INGRESS_ENQUEUE_PATH,
    IngressEnqueueError,
    IngressEnqueueRejected,
    IngressEnqueueUnreachable,
    IngressHttpTransport,
)
from ..spool import JsonFileSpool
from .rag_ready_document import DEFAULT_INGRESS_PAYLOAD_KIND, assert_no_secret_like_metadata


INGRESS_QUEUE_SCHEMA_VERSION = "rag_ingress_enqueue.v1"


@dataclass(frozen=True)
class OutboxItem:
    item_id: str
    path: Path
    status: str


class RagIngressHttpClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0):
        self._transport = IngressHttpTransport(base_url=base_url, timeout_seconds=timeout_seconds)
        self.base_url = self._transport.base_url
        self.timeout_seconds = self._transport.timeout_seconds

    def enqueue_document_payload(self, payload: dict) -> dict:
        validate_outbox_payload(payload)
        return self._transport.enqueue_json_payload(payload)


class FileBackedIngressOutbox:
    SUBDIRS = ("pending", "acked", "quarantine")

    def __init__(self, root: Path | str):
        self._spool = JsonFileSpool(root, subdirs=self.SUBDIRS, root_label="outbox")
        self.root = self._spool.root

    def enqueue(self, payload: dict) -> OutboxItem:
        validate_outbox_payload(payload)
        item_id = outbox_item_id(payload)
        filename = f"{item_id}.json"
        existing = self._spool.find_existing(filename)
        if existing is not None:
            return OutboxItem(item_id=item_id, path=existing, status=_status_for_path(existing))
        final_path = self._spool.write_json_once(filename, payload, separators=(",", ":"))
        return OutboxItem(item_id=item_id, path=final_path, status="pending")

    def flush(self, client, *, limit: int = 50) -> dict:
        sent = 0
        quarantined = 0
        stopped = False
        for path in self._spool.files("pending")[: max(int(limit), 1)]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                validate_outbox_payload(payload)
                client.enqueue_document_payload(payload)
            except IngressEnqueueUnreachable:
                stopped = True
                break
            except (IngressEnqueueRejected, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                self._spool.move_to(path, "quarantine")
                quarantined += 1
            else:
                self._spool.move_to(path, "acked")
                sent += 1
        counts = self.depth_counts()
        return {
            "sent": sent,
            "quarantined": quarantined,
            "stopped_unreachable": stopped,
            "pending": counts["pending"],
            "acked": counts["acked"],
            "quarantine": counts["quarantine"],
        }

    def depth_counts(self) -> dict[str, int]:
        return self._spool.depth_counts()

    def _find_existing(self, filename: str) -> Path | None:
        return self._spool.find_existing(filename)

    def _move(self, source: Path, subdir: str) -> Path:
        return self._spool.move_to(source, subdir)


def validate_outbox_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if payload.get("schemaVersion") != INGRESS_QUEUE_SCHEMA_VERSION:
        raise ValueError("unsupported ingress schemaVersion")
    document_payload = ((payload.get("payload") or {}).get("document") or {})
    if (payload.get("payload") or {}).get("kind") != DEFAULT_INGRESS_PAYLOAD_KIND:
        raise ValueError("unsupported ingress payload kind")
    if not document_payload.get("body"):
        raise ValueError("document.body is required")
    if not document_payload.get("filename"):
        raise ValueError("document.filename is required")
    if not payload.get("contentHash", "").startswith("sha256:"):
        raise ValueError("contentHash must be sha256")
    if not payload.get("targetProfile"):
        raise ValueError("targetProfile is required")
    if not payload.get("kind"):
        raise ValueError("kind is required")
    if not payload.get("idempotencyKey"):
        raise ValueError("idempotencyKey is required")
    metadata = document_payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("document.metadata must be an object")
    assert_no_secret_like_metadata(metadata)
    return payload


def outbox_item_id(payload: dict) -> str:
    idempotency_key = str(payload.get("idempotencyKey") or "")
    return "outbox_" + sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]


def _status_for_path(path: Path) -> str:
    return path.parent.name
