"""Private, immutable redacted delivery snapshots for multipart retries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .spool import JsonFileSpool


SNAPSHOT_SCHEMA_VERSION = "dendrite_transcript_delivery.v1"


class TranscriptOutbox:
    def __init__(self, root: Path | str):
        self._spool = JsonFileSpool(root, subdirs=("snapshots",), root_label="transcript outbox")

    def load(self, request_id: str) -> dict | None:
        path = self._spool.find_existing(f"{request_id}.json")
        if path is None:
            return None
        if path.is_symlink():
            raise ValueError("delivery_snapshot_invalid")
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("delivery_snapshot_invalid") from exc
        if not isinstance(snapshot, dict):
            raise ValueError("delivery_snapshot_invalid")
        unsigned = {k: v for k, v in snapshot.items() if k != "snapshot_hash"}
        if (
            snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
            or snapshot.get("request_id") != request_id
            or not isinstance(snapshot.get("documents"), list)
            or not snapshot["documents"]
            or snapshot.get("snapshot_hash") != _snapshot_hash(unsigned)
        ):
            raise ValueError("delivery_snapshot_invalid")
        return snapshot

    def create(self, request_id: str, *, target_profile: str, documents: list[dict]) -> dict:
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "request_id": request_id,
            "target_profile": target_profile,
            "documents": documents,
        }
        snapshot["snapshot_hash"] = _snapshot_hash(snapshot)
        self._spool.write_json_once(f"{request_id}.json", snapshot, subdir="snapshots")
        loaded = self.load(request_id)
        if loaded is None:
            raise ValueError("delivery_snapshot_missing")
        return loaded

    def discard(self, request_id: str) -> None:
        """Remove this shipper's snapshot only after the capture request is acked."""
        path = self._spool.find_existing(f"{request_id}.json")
        if path is not None:
            path.unlink(missing_ok=True)


def _snapshot_hash(value: dict) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
