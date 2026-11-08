"""Shared workspace-path helpers for capture normalization."""
from __future__ import annotations

from urllib.parse import unquote, urlparse


PROJECT_SOURCE_PATH_KEYS = (
    "workspacePath", "workspace_path", "workspaceRoot", "workspace_root", "cwd",
    "currentWorkingDirectory", "current_working_directory",
    "workingDirectory", "working_directory",
)


def usable_project_source_path(value) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip().rstrip("/")
    if not candidate or looks_like_provider_storage_path(candidate):
        return ""
    return candidate


def looks_like_provider_storage_path(value: str) -> bool:
    parts = [p.lower() for p in value.replace("\\", "/").split("/") if p]
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


def workspace_uri_to_path(value: str) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if not parsed.scheme:
        return candidate
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return ""
    return unquote(parsed.path)
