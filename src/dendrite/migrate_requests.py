"""Migration request builders."""
from __future__ import annotations

from pathlib import Path

from .transcript_capture import normalize_provider_capture_request


def build_migration_request(provider: str, path: Path, *, project: str = "") -> dict:
    payload = {"transcript_path": str(path)}
    return normalize_provider_capture_request(provider, payload, project=project)


def build_hermes_migration_request(
    db_path: Path, session_id: str, *, project: str = "", profile: str = "default"
) -> dict:
    payload = {"transcript_path": str(db_path), "session_id": session_id, "hermes_profile": profile}
    return normalize_provider_capture_request("hermes", payload, project=project)


def build_grok_migration_request(path: Path, *, project: str = "") -> dict:
    path = Path(path)
    session_id = path.parent.name
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(path),
        "session_id": session_id,
        "sessionId": session_id,
    }
    return normalize_provider_capture_request("grok", payload, project=project)


def build_opencode_migration_request(db_path: Path, session_id: str, *, project: str = "") -> dict:
    payload = {
        "hook_event_name": "session.idle",
        "transcript_path": str(db_path),
        "session_id": session_id,
        "sessionId": session_id,
        "sessionID": session_id,
    }
    return normalize_provider_capture_request("opencode", payload, project=project)
