"""Backwards-compatible re-export surface for `dendrite.transcript_capture`.

Implementation moved to:
- `dendrite.providers.normalize` (normalize/validate)
- `dendrite.providers.locators` (resolvers)
- `dendrite.providers.antigravity` (metadata helpers)
- `dendrite.providers.helpers_workspace` (workspace path)
- `dendrite.capture_spool` (TranscriptCaptureSpool)
"""
from .capture_spool import TranscriptCaptureSpool, MAX_QUARANTINE_RETRY_ATTEMPTS, NON_RECOVERABLE_FAILURE_CLASSES
from .providers.normalize import (
    CAPTURE_SCHEMA_VERSION,
    MAX_LOCATOR_CHARS,
    RAW_TRANSCRIPT_FIELDS,
    SUPPORTED_TRANSCRIPT_PROVIDERS,
    SOURCE_UNPROVEN_PROVIDERS,
    PROJECT_SOURCE_PATH_KEYS,
    canonicalize_project,
    has_workspace_path,
    normalize_provider_capture_request,
    validate_capture_request,
    validate_locator_value,
    extract_source_locator as _extract_source_locator,
    capture_event_type as _capture_event_type,
    source_locator_version_hash as _source_locator_version_hash,
    assert_public_surfaces_are_redacted as _assert_public_surfaces_are_redacted,
    now_iso as _now_iso,
    sha256 as _sha256,
    first_workspace_path as _first_workspace_path,
    resolve_project as _resolve_project,
)
import sqlite3  # noqa: F401  (monkeypatch surface)
from pathlib import Path  # noqa: F401  (monkeypatch surface)

# Provider-specific constants kept for back-compat.
HERMES_DEFAULT_STATE_DB = ".hermes/state.db"
HERMES_LOCATOR_KEYS = ("hermes_db_path", "state_db_path", "session_db_path")
OPENCODE_DEFAULT_STATE_DB = ".local/share/opencode/opencode.db"
OMP_DEFAULT_AGENT_DIR = ".omp/agent"
OMP_DIR_ENV = "PI_CODING_AGENT_DIR"

__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "MAX_LOCATOR_CHARS",
    "RAW_TRANSCRIPT_FIELDS",
    "SUPPORTED_TRANSCRIPT_PROVIDERS",
    "SOURCE_UNPROVEN_PROVIDERS",
    "PROJECT_SOURCE_PATH_KEYS",
    "canonicalize_project",
    "has_workspace_path",
    "normalize_provider_capture_request",
    "validate_capture_request",
    "TranscriptCaptureSpool",
    "MAX_QUARANTINE_RETRY_ATTEMPTS",
    "NON_RECOVERABLE_FAILURE_CLASSES",
]
