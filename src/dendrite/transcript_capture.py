from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import time

from .redaction import redact_text_v2

CAPTURE_SCHEMA_VERSION = "agent_knowledge_capture_request.v1"
MAX_LOCATOR_CHARS = 4096
RAW_TRANSCRIPT_FIELDS = {
    "conversation",
    "messages",
    "raw_messages",
    "raw_transcript",
    "transcript",
    "transcript_content",
    "turns",
}
SUPPORTED_TRANSCRIPT_PROVIDERS = {"claude", "gemini", "codex", "antigravity"}
SOURCE_UNPROVEN_PROVIDERS: set[str] = set()
CODEX_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,160}$")
PROJECT_SOURCE_PATH_KEYS = (
    "workspacePath",
    "workspace_path",
    "cwd",
    "currentWorkingDirectory",
    "current_working_directory",
    "workingDirectory",
    "working_directory",
)
MAX_QUARANTINE_RETRY_ATTEMPTS = 3
NON_RECOVERABLE_FAILURE_CLASSES = {
    "source_parse_failed",
    "source_policy_blocked",
    "source_unproven",
    "source_unreadable",
}


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonicalize_project(project: str) -> str:
    value = str(project or "")
    if not value:
        return ""
    if "/" not in value and "\\" not in value:
        return _canonicalize_project_slug(value) or value
    parts = [part for part in value.replace("\\", "/").split("/") if part]
    if not parts:
        return ""
    lower_parts = [part.lower() for part in parts]
    for marker in (".claude-worktrees", "claude-worktrees"):
        if marker in lower_parts:
            marker_index = lower_parts.index(marker)
            parts = parts[:marker_index]
            lower_parts = lower_parts[:marker_index]
            break
    if ".openclaw" in lower_parts:
        marker_index = lower_parts.index(".openclaw")
        if marker_index + 1 < len(parts):
            return parts[marker_index + 1]
    if "projects" in lower_parts:
        marker_index = lower_parts.index("projects")
        if marker_index + 1 < len(parts):
            return parts[marker_index + 1]
    return parts[-1]


def _canonicalize_project_slug(value: str) -> str | None:
    normalized = re.sub(r"-+", "-", value.strip("-"))
    lower = normalized.lower()
    if not normalized:
        return ""
    openclaw_marker = "openclaw-"
    if openclaw_marker in lower and ("users-" in lower or "home-" in lower):
        tail = lower[lower.index(openclaw_marker) + len(openclaw_marker) :]
        if tail:
            return tail
    projects_marker = "projects-"
    if projects_marker in lower and ("users-" in lower or "home-" in lower):
        tail = lower[lower.index(projects_marker) + len(projects_marker) :]
        if tail:
            return tail
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_workspace_path(payload: dict) -> str:
    """Return the first usable session workspace/cwd path from a hook payload."""
    for path in _iter_project_source_paths(payload):
        return path
    return ""


def _iter_project_source_paths(payload: dict):
    paths = payload.get("workspacePaths")
    if isinstance(paths, list):
        for path in paths:
            usable = _usable_project_source_path(path)
            if usable:
                yield usable
    for key in PROJECT_SOURCE_PATH_KEYS:
        usable = _usable_project_source_path(payload.get(key))
        if usable:
            yield usable


def _usable_project_source_path(value) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip().rstrip("/")
    if not candidate or _looks_like_provider_storage_path(candidate):
        return ""
    return candidate


def _looks_like_provider_storage_path(value: str) -> bool:
    parts = [part.lower() for part in value.replace("\\", "/").split("/") if part]
    if _contains_subsequence(parts, [".codex", "sessions"]):
        return True
    if _contains_subsequence(parts, [".claude", "projects"]):
        return True
    if _contains_subsequence(parts, [".gemini", "antigravity-cli", "brain"]):
        return True
    return ".system_generated" in parts


def _contains_subsequence(parts: list[str], needle: list[str]) -> bool:
    if not needle:
        return True
    position = 0
    for part in parts:
        if part == needle[position]:
            position += 1
            if position == len(needle):
                return True
    return False


def has_workspace_path(payload: dict) -> bool:
    """True when the payload carries a usable session workspace/cwd path.

    Interactive agy Stop payloads carry it; headless ``agy --print`` sends an empty
    ``workspacePaths``. The global Stop hook uses this (via --require-workspace-path)
    to skip headless sessions and defer them to the launch-dir capture shim.
    """
    return bool(_first_workspace_path(payload))


def _resolve_project(payload: dict, fallback: str) -> str:
    """Label the capture by the session's own workspace directory.

    CLI surfaces (e.g. Antigravity `agy`) run in arbitrary directories but share a
    single global Stop hook, so a hardcoded ``--project`` would mislabel every
    session launched outside the configured fallback workspace. When the hook payload carries a
    session workspace/cwd path, use it; otherwise fall back to the operator-provided
    ``--project``.
    """
    first = _first_workspace_path(payload)
    if first:
        return canonicalize_project(first)
    return canonicalize_project(fallback)


def normalize_provider_capture_request(provider: str, payload: dict, *, project: str) -> dict:
    if provider not in SUPPORTED_TRANSCRIPT_PROVIDERS | SOURCE_UNPROVEN_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    raw_fields = sorted(RAW_TRANSCRIPT_FIELDS & set(payload))
    if raw_fields:
        raise ValueError(f"transcript content fields are not allowed in hook payload: {', '.join(raw_fields)}")

    project = _resolve_project(payload, project)
    session_id = str(payload.get("session_id") or _provider_session_id(provider, payload))
    locator = _extract_source_locator(provider, payload)
    locator_hash = _sha256(locator) if locator else ""
    locator_version_hash = _source_locator_version_hash(locator)
    source_status = "source_unproven" if provider in SOURCE_UNPROVEN_PROVIDERS else "source_locator_private_spool_only"
    event_type = _capture_event_type(provider, payload)
    identity = ":".join([provider, event_type, session_id or locator_hash, locator_hash, locator_version_hash])
    observed_at = str(payload.get("observed_at") or payload.get("timestamp") or _now_iso())
    request_id = "req_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "request_id": request_id,
        "provider": provider,
        "project": project,
        "event_type": event_type,
        "observed_at": observed_at,
        "session_id_hash": _sha256(f"{provider}:{session_id}"),
        "source_locator": {
            "kind": "provider_transcript_source",
            "status": source_status,
            "locator_hash": locator_hash,
            "locator_version_hash": locator_version_hash,
            "runtime_handle": locator,
            "raw_path_present": bool(locator),
        },
        "capabilities": {
            "transcript_content_in_event": False,
            "hook_called_ragflow": False,
            "provider_execution_mutated": False,
        },
        "redaction_version": "redaction.v2",
        "privacy_level": "private_session",
        "content_policy": "locator_only",
        "public_summary": {
            "provider": provider,
            "project": project,
            "source_status": source_status,
            "source_locator_hash": locator_hash,
            "source_locator_version_hash": locator_version_hash,
            "observed_at": observed_at,
        },
    }


def _extract_source_locator(provider: str, payload: dict) -> str:
    if provider in SOURCE_UNPROVEN_PROVIDERS:
        return ""
    for key in ("transcript_path", "transcriptPath", "source_locator", "runtime_handle"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            _validate_locator_value(value)
            return value
    if provider == "codex":
        locator = _resolve_codex_session_locator(payload)
        if locator:
            _validate_locator_value(locator)
            return locator
    return ""


def _provider_session_id(provider: str, payload: dict) -> str:
    if provider == "antigravity":
        return str(payload.get("conversationId") or payload.get("conversation_id") or "")
    return ""


def _resolve_codex_session_locator(payload: dict) -> str:
    session_id = str(payload.get("session_id") or "")
    if not CODEX_SESSION_ID_PATTERN.fullmatch(session_id):
        return ""
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    sessions_root = codex_home / "sessions"
    if not sessions_root.is_dir():
        return ""
    candidates = [path for path in sessions_root.rglob(f"*{session_id}*.jsonl") if path.is_file()]
    if not candidates:
        return ""
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def _source_locator_version_hash(locator: str) -> str:
    if not locator:
        return ""
    try:
        path = Path(locator)
        if path.is_symlink() or not path.is_file():
            return ""
        stat = path.stat()
    except OSError:
        return ""
    return _sha256(f"{stat.st_mtime_ns}:{stat.st_size}")


def _validate_locator_value(value: str) -> None:
    if len(value) > MAX_LOCATOR_CHARS:
        raise ValueError("source locator is too large")
    if "<redacted:secret>" in redact_text_v2(value):
        raise ValueError("source locator contains secret-shaped content")
    lowered = value.lower()
    if "\n" in value or "\r" in value:
        raise ValueError("source locator must be a single-line private handle")
    if "assistant:" in lowered or "user:" in lowered or "tool:" in lowered:
        raise ValueError("source locator looks like transcript content")
    if len(value.split()) > 1:
        raise ValueError("source locator must look like a handle, not prose")


def _capture_event_type(provider: str, payload: dict) -> str:
    hook_event_name = payload.get("hook_event_name")
    if provider == "gemini" and hook_event_name == "SessionEnd":
        return "session_end"
    if provider == "codex" and hook_event_name == "Stop":
        return "session_end"
    if provider == "codex" and hook_event_name == "UserPromptSubmit":
        return "user_prompt_submit"
    if provider == "claude" and hook_event_name in {"Stop", "SessionEnd"}:
        return "session_end"
    if provider == "antigravity" and (
        hook_event_name in {"Stop", "SessionEnd"} or payload.get("fullyIdle") is True or payload.get("terminationReason")
    ):
        return "session_end"
    return str(payload.get("event_type") or "session_end")


def validate_capture_request(request: dict) -> dict:
    required = {
        "schema_version",
        "request_id",
        "provider",
        "project",
        "event_type",
        "session_id_hash",
        "source_locator",
        "redaction_version",
        "privacy_level",
        "content_policy",
    }
    missing = sorted(required - set(request))
    if missing:
        raise ValueError(f"missing required capture request fields: {', '.join(missing)}")
    if request["schema_version"] != CAPTURE_SCHEMA_VERSION:
        raise ValueError("unsupported capture request schema_version")
    if request["content_policy"] != "locator_only":
        raise ValueError("capture request content_policy must be locator_only")
    if request["provider"] not in SUPPORTED_TRANSCRIPT_PROVIDERS | SOURCE_UNPROVEN_PROVIDERS:
        raise ValueError(f"unsupported provider: {request['provider']}")
    for field in RAW_TRANSCRIPT_FIELDS:
        if field in request:
            raise ValueError(f"forbidden raw transcript field: {field}")
    source_locator = request.get("source_locator") or {}
    if not isinstance(source_locator, dict):
        raise ValueError("source_locator must be an object")
    locator_hash = str(source_locator.get("locator_hash") or "")
    if locator_hash and not locator_hash.startswith("sha256:"):
        raise ValueError("locator_hash must be sha256")
    runtime_handle = source_locator.get("runtime_handle")
    if isinstance(runtime_handle, str) and runtime_handle:
        _validate_locator_value(runtime_handle)
    _assert_public_surfaces_are_redacted(request)
    return request


def _assert_public_surfaces_are_redacted(request: dict) -> None:
    public_summary = request.get("public_summary")
    if public_summary is not None:
        public_text = json.dumps(public_summary, sort_keys=True, ensure_ascii=False)
        if redact_text_v2(public_text) != public_text:
            raise ValueError("public capture summary contains private locator or secret")


class TranscriptCaptureSpool:
    SUBDIRS = ("pending", "processing", "acked", "quarantine")

    def __init__(self, root: Path | str):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("capture spool root must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        for subdir in self.SUBDIRS:
            path = self.root / subdir
            if path.is_symlink():
                raise ValueError(f"capture spool subdirectory must not be a symlink: {subdir}")
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)

    def enqueue(self, request: dict) -> Path:
        validate_capture_request(request)
        name = f"{request['request_id']}.json"
        existing = self._find_existing(name)
        if existing is not None:
            return existing
        final_path = self.root / "pending" / name
        temp_path = self.root / "pending" / f".{name}.tmp"
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(request, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, final_path)
            os.chmod(final_path, 0o600)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return final_path

    def _find_existing(self, name: str) -> Path | None:
        for subdir in self.SUBDIRS:
            candidate = self.root / subdir / name
            if candidate.exists():
                return candidate
        return None

    def claim_next(self) -> Path:
        pending = sorted((self.root / "pending").glob("*.json"))
        if not pending:
            raise FileNotFoundError("no pending transcript capture requests")
        source = pending[0]
        target = self.root / "processing" / source.name
        os.replace(source, target)
        return target

    def ack(self, processing_path: Path | str) -> Path:
        source = Path(processing_path)
        target = self.root / "acked" / source.name
        os.replace(source, target)
        return target

    def quarantine(self, processing_path: Path | str) -> Path:
        return self.quarantine_with_failure(processing_path)

    def quarantine_with_failure(self, processing_path: Path | str, failure: dict | None = None) -> Path:
        source = Path(processing_path)
        if failure is not None:
            request = json.loads(source.read_text(encoding="utf-8"))
            request["last_failure"] = dict(failure)
            _write_private_json(source, request)
        target = self.root / "quarantine" / source.name
        os.replace(source, target)
        return target

    def requeue_recoverable_quarantine(self, *, max_items: int = 1, max_attempts: int = MAX_QUARANTINE_RETRY_ATTEMPTS) -> int:
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
            _write_private_json(source, request)
            target = self.root / "pending" / source.name
            if target.exists():
                continue
            os.replace(source, target)
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
            os.replace(source, target)
            moved += 1
        return moved

    def depth_counts(self) -> dict[str, int]:
        return {subdir: len(list((self.root / subdir).glob("*.json"))) for subdir in self.SUBDIRS}


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


def _write_private_json(path: Path, payload: dict) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
