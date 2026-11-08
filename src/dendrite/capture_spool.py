"""Durable locator-only capture spool."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import time

from .spool import JsonFileSpool
from .providers.normalize import validate_capture_request

MAX_QUARANTINE_RETRY_ATTEMPTS = 3
NON_RECOVERABLE_FAILURE_CLASSES = {
    "source_parse_failed",
    "source_policy_blocked",
    "source_unproven",
    "source_unreadable",
}


class TranscriptCaptureSpool:
    SUBDIRS = ("pending", "processing", "acked", "quarantine")

    def __init__(self, root: Path | str):
        self._spool = JsonFileSpool(root, subdirs=self.SUBDIRS, root_label="capture spool")
        self.root = self._spool.root

    def enqueue(self, request: dict) -> Path:
        validate_capture_request(request)
        name = f"{request['request_id']}.json"
        return self._spool.write_json_once(name, request)

    def _find_existing(self, name: str) -> Path | None:
        return self._spool.find_existing(name)

    def claim_next(self) -> Path:
        return self._spool.claim_next(empty_message="no pending transcript capture requests")

    def ack(self, processing_path: Path | str) -> Path:
        return self._spool.ack(processing_path)

    def bump_delivery_attempt(self, processing_path: Path | str) -> int:
        path = Path(processing_path)
        request = json.loads(path.read_text(encoding="utf-8"))
        delivery = dict(request.get("delivery") or {})
        attempts = int(delivery.get("attempts") or 0) + 1
        delivery["attempts"] = attempts
        request["delivery"] = delivery
        self._spool.replace_json(path, request)
        return attempts

    def checkpoint_delivery(self, processing_path: Path | str, *, snapshot_hash: str, next_part: int) -> None:
        source = Path(processing_path)
        request = json.loads(source.read_text(encoding="utf-8"))
        request["delivery"] = {"snapshot_hash": snapshot_hash, "next_part": next_part}
        self._spool.replace_json(source, request)

    def quarantine(self, processing_path: Path | str) -> Path:
        return self.quarantine_with_failure(processing_path)

    def quarantine_with_failure(self, processing_path: Path | str, failure: dict | None = None) -> Path:
        source = Path(processing_path)
        if failure is not None:
            request = json.loads(source.read_text(encoding="utf-8"))
            request["last_failure"] = dict(failure)
            self._spool.replace_json(source, request)
        return self._spool.move_to(source, "quarantine")

    def requeue_with_failure(self, processing_path: Path | str, failure: dict | None = None) -> Path:
        source = Path(processing_path)
        if failure is not None:
            request = json.loads(source.read_text(encoding="utf-8"))
            request["last_failure"] = dict(failure)
            self._spool.replace_json(source, request)
        return self._spool.move_to(source, "pending")

    def requeue_recoverable_quarantine(
        self, *, max_items: int = 1, max_attempts: int = MAX_QUARANTINE_RETRY_ATTEMPTS
    ) -> int:
        moved = 0
        for source in sorted((self.root / "quarantine").glob("*.json")):
            if moved >= max(max_items, 0):
                break
            try:
                request = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not _is_recoverable_quarantine_request(request, max_attempts=max_attempts):
                continue
            recovery = dict(request.get("recovery") or {})
            recovery["retry_attempts"] = int(recovery.get("retry_attempts") or 0) + 1
            recovery["last_requeued_at"] = datetime.now(timezone.utc).isoformat()
            request["recovery"] = recovery
            self._spool.replace_json(source, request)
            target = self.root / "pending" / source.name
            if target.exists():
                continue
            self._spool.move_to(source, "pending")
            moved += 1
        return moved

    def requeue_stale_processing(self, *, max_age_seconds: float = 30.0, max_items: int = 10) -> int:
        moved = 0
        cutoff = time() - max(max_age_seconds, 0.0)
        for source in sorted((self.root / "processing").glob("*.json")):
            if moved >= max(max_items, 0):
                break
            if source.stat().st_mtime > cutoff:
                continue
            target = self.root / "pending" / source.name
            if target.exists():
                continue
            self._spool.move_to(source, "pending")
            moved += 1
        return moved

    def depth_counts(self) -> dict[str, int]:
        return self._spool.depth_counts()


def _is_recoverable_quarantine_request(request: dict, *, max_attempts: int) -> bool:
    source_locator = request.get("source_locator") or {}
    runtime_handle = source_locator.get("runtime_handle")
    if not isinstance(runtime_handle, str) or not runtime_handle:
        return False
    path = Path(runtime_handle)
    if path.is_symlink() or not path.is_file():
        return False
    last_failure = request.get("last_failure") or {}
    error_class = str(last_failure.get("error_class") or "")
    if error_class in NON_RECOVERABLE_FAILURE_CLASSES:
        return False
    recovery = request.get("recovery") or {}
    try:
        retry_attempts = int(recovery.get("retry_attempts") or 0)
    except (TypeError, ValueError):
        retry_attempts = 0
    return retry_attempts < max_attempts
