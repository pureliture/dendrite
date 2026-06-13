from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .rag_ready_document import DEFAULT_INGRESS_PAYLOAD_KIND, assert_no_secret_like_metadata


INGRESS_QUEUE_SCHEMA_VERSION = "rag_ingress_enqueue.v1"
INGRESS_ENQUEUE_PATH = "/v1/ingest/enqueue"


class IngressEnqueueError(RuntimeError):
    pass


class IngressEnqueueRejected(IngressEnqueueError):
    pass


class IngressEnqueueUnreachable(IngressEnqueueError):
    pass


@dataclass(frozen=True)
class OutboxItem:
    item_id: str
    path: Path
    status: str


class RagIngressHttpClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0):
        if not base_url:
            raise ValueError("base_url is required")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def enqueue_document_payload(self, payload: dict) -> dict:
        validate_outbox_payload(payload)
        request_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{INGRESS_ENQUEUE_PATH}",
            data=request_body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = _read_json_response(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise IngressEnqueueRejected(f"ingress enqueue rejected: http_{exc.code}") from exc
            raise IngressEnqueueUnreachable(f"ingress enqueue failed: http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IngressEnqueueUnreachable("ingress enqueue failed: unreachable") from exc
        if response_payload.get("accepted") is not True:
            status = str(response_payload.get("status") or "rejected")
            raise IngressEnqueueRejected(f"ingress enqueue rejected: {status}")
        return {
            "job_id": str(response_payload.get("jobId") or response_payload.get("job_id") or ""),
            "status": str(response_payload.get("status") or "queued"),
        }


class FileBackedIngressOutbox:
    SUBDIRS = ("pending", "acked", "quarantine")

    def __init__(self, root: Path | str):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("outbox root must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        for subdir in self.SUBDIRS:
            path = self.root / subdir
            if path.is_symlink():
                raise ValueError(f"outbox subdirectory must not be a symlink: {subdir}")
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)

    def enqueue(self, payload: dict) -> OutboxItem:
        validate_outbox_payload(payload)
        item_id = outbox_item_id(payload)
        filename = f"{item_id}.json"
        existing = self._find_existing(filename)
        if existing is not None:
            return OutboxItem(item_id=item_id, path=existing, status=_status_for_path(existing))
        final_path = self.root / "pending" / filename
        temp_path = self.root / "pending" / f".{filename}.tmp"
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, final_path)
            os.chmod(final_path, 0o600)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return OutboxItem(item_id=item_id, path=final_path, status="pending")

    def flush(self, client: RagIngressHttpClient, *, limit: int = 50) -> dict:
        sent = 0
        quarantined = 0
        stopped = False
        for path in sorted((self.root / "pending").glob("*.json"))[: max(int(limit), 1)]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                validate_outbox_payload(payload)
                client.enqueue_document_payload(payload)
            except IngressEnqueueUnreachable:
                stopped = True
                break
            except (IngressEnqueueRejected, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                self._move(path, "quarantine")
                quarantined += 1
            else:
                self._move(path, "acked")
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
        return {subdir: len(list((self.root / subdir).glob("*.json"))) for subdir in self.SUBDIRS}

    def _find_existing(self, filename: str) -> Path | None:
        for subdir in self.SUBDIRS:
            candidate = self.root / subdir / filename
            if candidate.exists():
                return candidate
        return None

    def _move(self, source: Path, subdir: str) -> Path:
        target = self.root / subdir / source.name
        os.replace(source, target)
        os.chmod(target, 0o600)
        return target


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


def _read_json_response(body: str) -> dict:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise IngressEnqueueRejected("ingress enqueue failed: invalid_json") from exc
    if not isinstance(payload, dict):
        raise IngressEnqueueRejected("ingress enqueue failed: invalid_json")
    return payload


def _status_for_path(path: Path) -> str:
    return path.parent.name
