from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact_public_ingress_text

PUBLIC_SCHEMA_VERSION = "dendrite_source_catalog.v1"
PRIVATE_SCHEMA_VERSION = "dendrite_source_private_index.v1"


@dataclass(frozen=True)
class SourceCatalogRecord:
    source_ref_id: str
    device_id_hash: str
    root_id: str
    relative_path_hash: str
    content_hash: str
    mtime: str
    size: int
    sync_policy: str
    permission_scope: str = "project"
    last_seen_at: str = ""
    deleted_at: str = ""
    revoked_at: str = ""
    derived_summary: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_source_catalog(
    *,
    root: str | Path,
    root_id: str,
    device_id: str,
    public_out: str | Path,
    private_index: str | Path,
    sync_policy: str = "metadata_only",
    permission_scope: str = "project",
    limit: int | None = None,
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError("source root must be a directory")
    _require_opaque(root_id, "root_id")
    if sync_policy not in {"local_only", "metadata_only", "derived_only", "full_sync"}:
        raise ValueError("unsupported sync_policy")
    device_id_hash = _sha256_text(device_id)
    observed_at = _now()
    prior_index = _read_private_index(private_index)
    prior_records = prior_index.get("records", {}) if prior_index.get("device_id_hash") == device_id_hash else {}
    current_records: dict[str, dict[str, Any]] = {}
    public_records: list[dict[str, Any]] = []
    scanned = 0
    for path in _iter_files(root_path):
        if limit is not None and scanned >= max(0, int(limit)):
            break
        relative = path.relative_to(root_path).as_posix()
        stat = path.stat()
        content_hash = _sha256_file(path)
        record = SourceCatalogRecord(
            source_ref_id=_source_ref_id(device_id_hash, root_id, relative, content_hash),
            device_id_hash=device_id_hash,
            root_id=root_id,
            relative_path_hash=_sha256_text(relative),
            content_hash=content_hash,
            mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            size=int(stat.st_size),
            sync_policy=sync_policy,
            permission_scope=permission_scope,
            last_seen_at=observed_at,
        )
        public_records.append(record.to_public_dict())
        current_records[record.source_ref_id] = {
            "absolute_path": str(path),
            "record": record.to_public_dict(),
        }
        scanned += 1

    deleted = 0
    current_path_keys = {entry["absolute_path"] for entry in current_records.values()}
    for prior in prior_records.values():
        absolute_path = str(prior.get("absolute_path") or "")
        if not absolute_path or absolute_path in current_path_keys:
            continue
        record = dict(prior.get("record") or {})
        if not record or record.get("deleted_at"):
            continue
        record["deleted_at"] = observed_at
        record["last_seen_at"] = str(record.get("last_seen_at") or observed_at)
        public_records.append(record)
        current_records[str(record["source_ref_id"])] = {
            "absolute_path": absolute_path,
            "record": record,
        }
        deleted += 1

    _write_public_records(public_out, public_records)
    _write_private_index(
        private_index,
        {
            "schema_version": PRIVATE_SCHEMA_VERSION,
            "device_id_hash": device_id_hash,
            "root_id": root_id,
            "updated_at": observed_at,
            "records": current_records,
        },
    )
    return {
        "schema_version": "dendrite_source_catalog_scan_result.v1",
        "status": "ok",
        "device_id_hash": device_id_hash,
        "root_id": root_id,
        "records_written": len(public_records),
        "active_records": scanned,
        "deleted_records": deleted,
        "public_out_written": True,
        "private_index_written": True,
        "raw_paths_printed": False,
    }


def resolve_source_ref(
    *,
    private_index: str | Path,
    source_ref_id: str,
    requesting_device_id: str,
    approval_ref: str = "",
    expected_content_hash: str = "",
    max_bytes: int = 4096,
) -> dict[str, Any]:
    index = _read_private_index(private_index)
    device_id_hash = _sha256_text(requesting_device_id)
    if index.get("device_id_hash") != device_id_hash:
        return _response("same_device_required", "same_device_proof_failed", same_device_proof="failed")
    entry = (index.get("records") or {}).get(source_ref_id)
    if not entry:
        return _response("unresolved", "source_ref_not_found", same_device_proof="passed")
    record = dict(entry.get("record") or {})
    if record.get("deleted_at"):
        return _response("deleted_source", "source_deleted", record=record, same_device_proof="passed")
    if record.get("revoked_at"):
        return _response("permission_revoked", "permission_revoked", record=record, same_device_proof="passed")
    absolute_path = Path(str(entry.get("absolute_path") or ""))
    if not absolute_path.exists():
        return _response("deleted_source", "source_missing", record=record, same_device_proof="passed")
    current_hash = _sha256_file(absolute_path)
    if expected_content_hash and expected_content_hash != current_hash:
        return _response("stale_hash", "content_hash_mismatch", record=record, same_device_proof="passed")
    if current_hash != record.get("content_hash"):
        return _response("stale_hash", "catalog_hash_mismatch", record=record, same_device_proof="passed")
    if not approval_ref:
        return _response("approval_required", "approval_required", record=record, same_device_proof="passed")
    max_len = max(1, min(int(max_bytes), 65536))
    content = absolute_path.read_bytes()[:max_len].decode("utf-8", errors="replace")
    return _response(
        "resolved",
        "approved_same_device",
        record=record,
        same_device_proof="passed",
        approval_ref=approval_ref,
        content=redact_public_ingress_text(content),
    )


def _response(
    resolution_state: str,
    reason_code: str,
    *,
    record: dict[str, Any] | None = None,
    same_device_proof: str,
    approval_ref: str = "",
    content: str = "",
) -> dict[str, Any]:
    metadata = dict(record or {})
    metadata.pop("absolute_path", None)
    return {
        "schema_version": "dendrite_source_resolve_result.v1",
        "resolution_state": resolution_state,
        "reason_code": reason_code,
        "same_device_proof": same_device_proof,
        "approval_ref": approval_ref,
        "content": content,
        "metadata": metadata,
    }


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "__pycache__"} for part in path.relative_to(root).parts):
            continue
        yield path


def _write_public_records(path: str | Path, records: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")


def _write_private_index(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)


def _read_private_index(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {"schema_version": PRIVATE_SCHEMA_VERSION, "records": {}}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.get("schema_version") != PRIVATE_SCHEMA_VERSION:
        raise ValueError("unsupported source private index")
    return parsed


def _source_ref_id(device_id_hash: str, root_id: str, relative_path: str, content_hash: str) -> str:
    return "src_" + hashlib.sha256(
        "|".join([device_id_hash, root_id, relative_path, content_hash]).encode()
    ).hexdigest()[:24]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _require_opaque(value: str, name: str) -> None:
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"{name} must be opaque")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
