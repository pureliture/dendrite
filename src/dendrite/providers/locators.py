"""Provider session-store locator resolvers.

Every resolver returns a private path WITHOUT reading the file content.
An absent/symlinked explicit path yields "" (never a fabricated path).
"""
from __future__ import annotations

import os
from pathlib import Path


def _existing_non_symlink_store(candidate: Path) -> str:
    """Return the store path only if it is a real file (not a symlink); else ""."""
    if candidate.is_file() and not candidate.is_symlink():
        return str(candidate)
    return ""


def resolve_hermes_session_locator(payload: dict) -> str:
    """Hermes SQLite store path (read-side only, never opened here)."""
    from ..hermes_profiles import (
        DEFAULT_HERMES_PROFILE,
        default_hermes_home,
        hermes_profile_from_payload,
        state_db_for_profile,
    )

    for key in (
        "hermes_db_path", "state_db_path", "session_db_path",
        "transcript_path", "transcriptPath", "source_locator", "runtime_handle",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return _existing_non_symlink_store(Path(value))
    hermes_home = os.environ.get("HERMES_HOME")
    requested = _requested_hermes_profile(payload)
    if hermes_home:
        default_store = Path(hermes_home) / "state.db"
    elif requested != DEFAULT_HERMES_PROFILE:
        default_store = state_db_for_profile(default_hermes_home(), requested)
    else:
        default_store = Path.home() / ".hermes/state.db"
    return _existing_non_symlink_store(default_store)


def _requested_hermes_profile(payload: dict) -> str:
    from ..hermes_profiles import DEFAULT_HERMES_PROFILE, hermes_profile_from_payload

    for key in ("hermes_profile", "profile", "profile_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return hermes_profile_from_payload({key: value})
    return DEFAULT_HERMES_PROFILE


def _default_opencode_db_path() -> Path:
    explicit = os.environ.get("OPENCODE_DB_PATH")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("OPENCODE_HOME")
    if home:
        return Path(home).expanduser() / "opencode.db"
    return Path.home() / ".local/share/opencode/opencode.db"


def resolve_opencode_session_locator(payload: dict) -> str:
    for key in (
        "opencode_db_path", "state_db_path", "session_db_path",
        "transcript_path", "transcriptPath", "source_locator", "runtime_handle",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return _existing_non_symlink_store(Path(value).expanduser())
    return _existing_non_symlink_store(_default_opencode_db_path())


def opencode_session_id(payload: dict) -> str:
    return str(
        payload.get("session_id") or payload.get("sessionId") or payload.get("sessionID") or ""
    )


def default_omp_sessions_dir() -> Path:
    explicit = os.environ.get("PI_CODING_AGENT_DIR")
    if explicit:
        return Path(explicit).expanduser() / "sessions"
    return Path.home() / ".omp/agent/sessions"


def resolve_omp_session_locator(payload: dict) -> str:
    for key in (
        "omp_state_db_path", "state_db_path", "session_db_path",
        "transcript_path", "transcriptPath", "source_locator", "runtime_handle",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return _existing_non_symlink_store(Path(value).expanduser())
    session_id = omp_session_id(payload)
    sessions_dir = default_omp_sessions_dir()
    if not session_id or not sessions_dir.is_dir():
        return ""
    for candidate in sessions_dir.glob(f"*_{session_id}.jsonl"):
        if candidate.is_file() and not candidate.is_symlink():
            return str(candidate)
    return ""


def omp_session_id(payload: dict) -> str:
    for key in ("session_id", "sessionId", "sessionID"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def grok_session_id(payload: dict) -> str:
    return str(payload.get("sessionId") or payload.get("session_id") or "")


def resolve_grok_session_locator(payload: dict) -> str:
    for key in ("transcript_path", "transcriptPath", "source_locator", "runtime_handle"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            existing = _existing_non_symlink_store(Path(value))
            if existing:
                return existing
    session_id = grok_session_id(payload)
    if not session_id or not _valid_session_id(session_id):
        return ""
    sessions_root = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok")) / "sessions"
    if not sessions_root.is_dir():
        return ""
    try:
        matches = [
            path for path in sessions_root.rglob("updates.jsonl")
            if path.is_file() and not path.is_symlink() and path.parent.name == session_id
        ]
        if not matches:
            return ""
        best: Path | None = None
        best_mtime = -1
        for path in matches:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= best_mtime:
                best_mtime = mtime
                best = path
        return str(best) if best is not None else ""
    except OSError:
        return ""


def resolve_codex_session_locator(payload: dict) -> str:
    session_id = str(payload.get("session_id") or "")
    if not _valid_session_id(session_id):
        return ""
    sessions_root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"
    if not sessions_root.is_dir():
        return ""
    candidates = [p for p in sessions_root.rglob(f"*{session_id}*.jsonl") if p.is_file()]
    if not candidates:
        return ""
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def _valid_session_id(session_id: str) -> bool:
    import re

    return bool(re.fullmatch(r"^[A-Za-z0-9_.:-]{8,160}$", session_id or ""))


def provider_session_id(provider: str, payload: dict) -> str:
    if provider == "antigravity":
        return str(payload.get("conversationId") or payload.get("conversation_id") or "")
    if provider == "grok":
        return grok_session_id(payload)
    if provider == "opencode":
        return opencode_session_id(payload)
    if provider == "omp":
        return omp_session_id(payload)
    return ""
