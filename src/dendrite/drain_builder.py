"""Packed transcript document builder (markdown + yaml envelope)."""
from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from .transcript_source import MAX_TRANSCRIPT_BODY_CHARS, adapter_for

PARSER_VERSION = "dendrite-thin-transcript-drain.v2"


@dataclass(frozen=True)
class PackedTranscriptDocument:
    kind: str
    title: str
    body: str
    metadata: dict
    filename: str


def build_drain_document(request: dict) -> PackedTranscriptDocument:
    documents = build_drain_documents(request)
    if len(documents) != 1:
        raise ValueError("transcript_requires_multiple_parts")
    return documents[0]


def build_drain_documents(request: dict) -> list[PackedTranscriptDocument]:
    redacted_source = adapter_for(str(request["provider"])).read_redacted_transcript(request)
    observed_at = str(request.get("observed_at") or now_iso())
    source_hash = sha256(redacted_source)
    starts, ends = [], []
    offset = 0
    for line in redacted_source.splitlines(keepends=True):
        if line.strip():
            starts.append(offset)
            ends.append(offset + len(line))
        offset += len(line)
    turn_count = max(len(starts), 1)
    ceiling = max(len(redacted_source), 1)
    envelope = pack_part(
        request, "", observed_at=observed_at, source_hash=source_hash,
        char_start=len(redacted_source), char_end=len(redacted_source),
        part_index=ceiling, part_count=ceiling,
        turn_start=turn_count, turn_end=turn_count,
    )
    import json

    budget = MAX_TRANSCRIPT_BODY_CHARS - len(json.dumps(envelope.body, ensure_ascii=True))
    if budget < 12:
        raise ValueError("document_envelope_too_large")
    spans = split_source(redacted_source, budget)
    documents = []
    for index, (start, end) in enumerate(spans, 1):
        turn_start = min(bisect_right(ends, start) + 1, turn_count)
        turn_end = max(turn_start, bisect_left(starts, end))
        packed = pack_part(
            request, redacted_source[start:end], observed_at=observed_at, source_hash=source_hash,
            char_start=start, char_end=end, part_index=index, part_count=len(spans),
            turn_start=turn_start, turn_end=turn_end,
        )
        if len(json.dumps(packed.body, ensure_ascii=True)) > MAX_TRANSCRIPT_BODY_CHARS:
            raise ValueError("document_part_too_large")
        documents.append(packed)
    return documents


def split_source(text: str, budget: int) -> list[tuple[int, int]]:
    if not text:
        return [(0, 0)]
    spans = []
    start = 0
    while start < len(text):
        end, used = start, 0
        while end < len(text):
            char = text[end]
            code = ord(char)
            if char in ('"', "\\", "\b", "\f", "\n", "\r", "\t"):
                cost = 2
            elif code > 0xFFFF:
                cost = 12
            elif code < 32 or code >= 127:
                cost = 6
            else:
                cost = 1
            if used + cost > budget:
                break
            used += cost
            end += 1
        if end == start:
            raise ValueError("document_part_too_large")
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline >= start + (end - start) // 2:
                end = newline + 1
        spans.append((start, end))
        start = end
    return spans


def pack_part(
    request: dict, source_part: str, *, observed_at: str, source_hash: str,
    char_start: int, char_end: int, part_index: int, part_count: int,
    turn_start: int, turn_end: int,
) -> PackedTranscriptDocument:
    from .drain_utils import hash_fragment, slug, compact_utc_timestamp, sha256

    provider = str(request["provider"])
    project = str(request["project"])
    locator = request.get("source_locator") or {}
    session_id_hash = str(request["session_id_hash"])
    source_locator_hash = str(locator.get("locator_hash") or "")
    identity = f"{session_id_hash}:{request['request_id']}:{source_hash}:{char_start}:{char_end}"
    chunk_id = f"conversation_{hash_fragment(sha256(identity), 40)}"
    agent_id = str(request.get("agent_id") or f"{slug(provider)}-transcript-capture")
    content = "\n".join([
        "# Conversation Chunk", "",
        "## Context", "",
        f"- provider: {provider}",
        f"- project: {project}",
        f"- session_id_hash: {hash_fragment(session_id_hash, 12)}",
        f"- turn_range: {turn_start}-{turn_end}",
        f"- part: {part_index}/{part_count}",
        "- currentness: historical_conversation_memory",
        "",
        "## Transcript",
        "",
        "",
    ]) + source_part
    metadata = {
        "schema_version": "agent_knowledge_document.v2",
        "result_type": "conversation_chunk",
        "knowledge_id": f"kn_{hash_fragment(sha256(chunk_id + content), 24)}",
        "provider": provider,
        "project": project,
        "agent_id": agent_id,
        "session_id_hash": session_id_hash,
        "source_locator_hash": source_locator_hash,
        "chunk_id": chunk_id,
        "turn_start_index": turn_start,
        "turn_end_index": turn_end,
        "part_index": part_index,
        "part_count": part_count,
        "char_start": char_start,
        "char_end": char_end,
        "capture_snapshot_hash": source_hash,
        "observed_at_start": observed_at,
        "observed_at_end": observed_at,
        "privacy_level": "private",
        "redaction_version": "redaction.v2",
        "parser_version": PARSER_VERSION,
        "source_status": str(locator.get("status") or "source_locator_private_spool_only"),
        "domain": "agent_memory",
        "type": "conversation_chunk",
        "capture_request_id": str(request["request_id"]),
        "provider_source_contract": f"{provider}-transcript-source.v1",
        "ledger_contract": "agent_knowledge_ledger.v3",
        "retention_policy": "private_indefinite_until_disabled",
        "supersedes": "",
    }
    if provider == "hermes" and request.get("hermes_profile"):
        metadata["hermes_profile"] = str(request["hermes_profile"])
    body = _render_markdown(metadata, content)
    content_hash = sha256(body)
    filename = (
        f"ak-conv-{slug(provider)}-{slug(project)}-{hash_fragment(session_id_hash, 12)}"
        f"-t{turn_start:04d}-{turn_end:04d}-p{part_index:06d}-of-{part_count:06d}"
        f"-{compact_utc_timestamp(observed_at)}-{hash_fragment(content_hash, 12)}.md"
    )
    return PackedTranscriptDocument(
        kind="conversation_chunk",
        title=f"{provider} conversation chunk {part_index}/{part_count}",
        body=body,
        metadata=metadata,
        filename=filename,
    )


def _render_markdown(metadata: dict, content: str) -> str:
    return f"---\n{_render_yaml(metadata)}---\n{content}"


def _render_yaml(value: dict) -> str:
    lines = []
    for key, item in value.items():
        lines.append(f"{key}: {_yaml_scalar(item)}")
    return "\n".join(lines) + "\n"


def _yaml_scalar(value) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:/@+-")
    if all(char in safe for char in text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def queue_source(request: dict) -> dict[str, str]:
    source = {
        "host": "mac_mini",
        "producer": "dendrite-transcript-drain",
        "provider": str(request["provider"]),
        "project": str(request["project"]),
    }
    if request.get("provider") == "hermes" and request.get("hermes_profile"):
        source["hermes_profile"] = str(request["hermes_profile"])
    return source


# Kept for internal compat
def sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
