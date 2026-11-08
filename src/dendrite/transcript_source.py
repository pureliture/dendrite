"""Backwards-compatible re-export for transcript_source."""
from __future__ import annotations

from .providers.registry import adapter_kind_for
from .source_adapters.hermes import HermesSqliteSourceAdapter, enumerate_hermes_sessions, hermes_ro_uri, read_hermes_session_text
from .source_adapters.jsonl_adapter import JsonlSourceAdapter, OmpJsonlSourceAdapter, read_omp_session_text
from .source_adapters.opencode import (
    OpenCodeSqliteSourceAdapter, enumerate_opencode_sessions, opencode_ro_uri,
    read_opencode_session_text,
)
from .source_adapters.base import TranscriptSourceAdapter
from .source_adapters.common import source_path as _source_path

MAX_TRANSCRIPT_BODY_CHARS = 180_000


def adapter_for(provider: str) -> TranscriptSourceAdapter:
    kind = adapter_kind_for(provider)
    if kind == "hermes-sqlite":
        return HermesSqliteSourceAdapter()
    if kind == "opencode-sqlite":
        return OpenCodeSqliteSourceAdapter()
    if kind == "omp-jsonl":
        return OmpJsonlSourceAdapter()
    return JsonlSourceAdapter()


__all__ = [
    "MAX_TRANSCRIPT_BODY_CHARS",
    "TranscriptSourceAdapter",
    "JsonlSourceAdapter",
    "HermesSqliteSourceAdapter",
    "OpenCodeSqliteSourceAdapter",
    "OmpJsonlSourceAdapter",
    "adapter_for",
    "enumerate_hermes_sessions",
    "enumerate_opencode_sessions",
    "read_hermes_session_text",
    "read_opencode_session_text",
    "read_omp_session_text",
]
