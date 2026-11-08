"""Capture normalization and request validation.

`normalize_provider_capture_request` builds the locator-only capture
request from a provider hook payload. `validate_capture_request`
enforces the same schema at spool-queue entry points.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..hermes_profiles import (
    hermes_agent_id,
    hermes_profile_from_payload,
    hermes_session_hash_seed,
)
from ..redaction import redact_text_v2
from .locators import (
    provider_session_id,
    resolve_grok_session_locator,
    resolve_hermes_session_locator,
    resolve_opencode_session_locator,
    resolve_omp_session_locator,
    resolve_codex_session_locator,
)
from .registry import supported_names

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
SUPPORTED_TRANSCRIPT_PROVIDERS = frozenset(supported_names())
SOURCE_UNPROVEN_PROVIDERS: set[str] = set()
PROJECT_SOURCE_PATH_KEYS = (
    "workspacePath", "workspace_path", "workspaceRoot", "workspace_root", "cwd",
    "currentWorkingDirectory", "current_working_directory",
    "workingDirectory", "working_directory",
)


def sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonicalize_project(project: str) -> str:
    import re

    value = str(project or "")
    if not value:
        return ""
    if "/" not in value and "\\" not in value:
        return _canonicalize_project_slug(value) or value
    parts = [p for p in value.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    lower_parts = [p.lower() for p in parts]
    for marker in (".claude-worktrees", "claude-worktrees"):
        if marker in lower_parts:
            idx = lower_parts.index(marker)
            parts = parts[:idx]
            lower_parts = lower_parts[:idx]
            break
    if "projects" in lower_parts:
        idx = lower_parts.index("projects")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1]


def _canonicalize_project_slug(value: str) -> str | None:
    import re

    normalized = re.sub(r"-+", "-", value.strip("-"))
    lower = normalized.lower()
    if not normalized:
        return ""
    marker = "projects-"
    if marker in lower and ("users-" in lower or "home-" in lower):
        tail = lower[lower.index(marker) + len(marker):]
        if tail:
            return tail
    return None


def first_workspace_path(payload: dict) -> str:
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
    parts = [p.lower() for p in value.replace("\\", "/").split("/") if p]
    needle = lambda ps, ns: any(p == ns[0] for p in ps)
    if _contains_subsequence(parts, [".codex", "sessions"]):
        return True
    if _contains_subsequence(parts, [".claude", "projects"]):
        return True
    if _contains_subsequence(parts, [".gemini", "antigravity-cli", "brain"]):
        return True
    if _contains_subsequence(parts, [".grok", "sessions"]) or ".grok" in parts:
        return True
    if ".hermes" in parts:
        return True
    if "opencode.db" in parts or ".opencode" in parts:
        return True
    if _contains_subsequence(parts, ["opencode", "opencode.db"]):
        return True
    return ".system_generated" in parts


def _contains_subsequence(parts: list[str], needle: list[str]) -> bool:
    if not needle:
        return True
    pos = 0
    for part in parts:
        if part == needle[pos]:
            pos += 1
            if pos == len(needle):
                return True
    return False


def has_workspace_path(payload: dict) -> bool:
    return bool(first_workspace_path(payload))


def resolve_project(payload: dict, fallback: str, *, provider: str = "") -> str:
    first = first_workspace_path(payload)
    if first:
        return canonicalize_project(first)
    if provider == "antigravity":
        conversation_id = provider_session_id(provider, payload)
        from .antigravity import resolve_antigravity_project, session_project_fallback

        metadata_project = resolve_antigravity_project(conversation_id)
        if metadata_project:
            return canonicalize_project(metadata_project)
        if conversation_id:
            return session_project_fallback(conversation_id)
    return canonicalize_project(fallback)


def normalize_provider_capture_request(provider: str, payload: dict, *, project: str) -> dict:
    if provider not in SUPPORTED_TRANSCRIPT_PROVIDERS | SOURCE_UNPROVEN_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    raw_fields = sorted(RAW_TRANSCRIPT_FIELDS & set(payload))
    if raw_fields:
        raise ValueError(f"transcript content fields are not allowed in hook payload: {', '.join(raw_fields)}")

    session_id = str(payload.get("session_id") or provider_session_id(provider, payload))
    project = resolve_project(payload, project, provider=provider)
    locator = extract_source_locator(provider, payload)
    hermes_profile = hermes_profile_from_payload(payload, locator=locator) if provider == "hermes" else ""
    agent_id = hermes_agent_id(hermes_profile) if provider == "hermes" else f"{provider}-transcript-capture"
    locator_hash = sha256(locator) if locator else ""
    locator_version_hash = source_locator_version_hash(locator)
    source_status = "source_unproven" if provider in SOURCE_UNPROVEN_PROVIDERS else "source_locator_private_spool_only"
    event_type = capture_event_type(provider, payload)
    identity = ":".join([
        provider, event_type, hermes_profile, session_id or locator_hash,
        locator_hash, locator_version_hash,
    ])
    observed_at = str(payload.get("observed_at") or payload.get("timestamp") or now_iso())
    request_id = "req_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    session_hash_seed = (
        hermes_session_hash_seed(session_id, hermes_profile) if provider == "hermes"
        else f"{provider}:{session_id}"
    )
    public_summary = {
        "provider": provider,
        "project": project,
        "source_status": source_status,
        "source_locator_hash": locator_hash,
        "source_locator_version_hash": locator_version_hash,
        "observed_at": observed_at,
    }
    request_extra = {}
    if provider == "hermes":
        request_extra = {"hermes_profile": hermes_profile, "agent_id": agent_id}
        public_summary.update(request_extra)

    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "request_id": request_id,
        "provider": provider,
        "project": project,
        "event_type": event_type,
        "observed_at": observed_at,
        "session_id_hash": sha256(session_hash_seed),
        **request_extra,
        "session_id": session_id,
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
            "hook_called_backend": False,
            "provider_execution_mutated": False,
        },
        "redaction_version": "redaction.v2",
        "privacy_level": "private_session",
        "content_policy": "locator_only",
        "public_summary": public_summary,
    }


def extract_source_locator(provider: str, payload: dict) -> str:
    if provider in SOURCE_UNPROVEN_PROVIDERS:
        return ""
    if provider in {"hermes", "grok", "opencode", "omp"}:
        if provider == "hermes":
            locator = resolve_hermes_session_locator(payload)
        elif provider == "opencode":
            locator = resolve_opencode_session_locator(payload)
        elif provider == "omp":
            locator = resolve_omp_session_locator(payload)
        else:
            locator = resolve_grok_session_locator(payload)
        if locator:
            validate_locator_value(locator)
            return locator
        return ""
    for key in ("transcript_path", "transcriptPath", "source_locator", "runtime_handle"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            validate_locator_value(value)
            return value
    if provider == "codex":
        locator = resolve_codex_session_locator(payload)
        if locator:
            validate_locator_value(locator)
            return locator
    return ""


def source_locator_version_hash(locator: str) -> str:
    if not locator:
        return ""
    try:
        path = Path(locator)
        if path.is_symlink() or not path.is_file():
            return ""
        stat = path.stat()
    except OSError:
        return ""
    return sha256(f"{stat.st_mtime_ns}:{stat.st_size}")


def validate_locator_value(value: str) -> None:
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


def capture_event_type(provider: str, payload: dict) -> str:
    hook_event_name = payload.get("hook_event_name") or payload.get("hookEventName")
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
    if provider == "hermes" and hook_event_name in {"Stop", "SessionEnd", "session_end", "session:end", "on_session_end", "on_session_finalize"}:
        return "session_end"
    if provider == "grok" and hook_event_name in {"Stop", "stop", "SessionEnd", "session_end"}:
        return "session_end"
    if provider == "opencode" and hook_event_name in {"session.idle", "session_idle", "session:idle", "idle", "Stop", "stop", "SessionEnd", "session_end"}:
        return "session_end"
    if provider == "omp" and hook_event_name in {"agent_end", "agentEnd", "session_shutdown", "sessionShutdown", "Stop", "stop", "SessionEnd", "session_end"}:
        return "session_end"
    return str(payload.get("event_type") or "session_end")


def validate_capture_request(request: dict) -> dict:
    required = {
        "schema_version", "request_id", "provider", "project", "event_type",
        "session_id_hash", "source_locator", "redaction_version", "privacy_level", "content_policy",
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
        validate_locator_value(runtime_handle)
    assert_public_surfaces_are_redacted(request)
    return request


def assert_public_surfaces_are_redacted(request: dict) -> None:
    public_summary = request.get("public_summary")
    if public_summary is not None:
        public_text = json.dumps(public_summary, sort_keys=True, ensure_ascii=False)
        if redact_text_v2(public_text) != public_text:
            raise ValueError("public capture summary contains private locator or secret")
