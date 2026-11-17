from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dendrite.drain_builder import build_drain_documents, queue_source
from dendrite.drain_utils import sha256
from dendrite.redaction import redact_public_ingress_text
from dendrite.transcript_ingest import build_ingress_enqueue_request_body

# Mirrors neurons scripts/redaction-denylist.txt (compiled with Pattern.CASE_INSENSITIVE).
INGRESS_DENYLIST = (
    re.compile(r"Bearer\s+[A-Za-z0-9][A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"Basic\s+[A-Za-z0-9+/=]{8,}", re.IGNORECASE),
    re.compile(
        r"""\b(?:access[_-]?token|api[_-]?key|token|secret|password|passwd)\b["':=\s]{1,4}[A-Za-z0-9._/+\-]{20,}""",
        re.IGNORECASE,
    ),
    re.compile(r"""/Users/[^ \n\t'"]+/\.[^/ \n\t'"]+/private/[^ \n\t'"]+""", re.IGNORECASE),
    re.compile(r"""/Users/[^ \n\t'"]+/\.(claude|gemini|codex)/[^ \n\t'"]+""", re.IGNORECASE),
)


def _denylist_hits(text: str) -> list[str]:
    return [pat.pattern for pat in INGRESS_DENYLIST if pat.search(text)]


@pytest.mark.parametrize(
    "benign_text",
    [
        "use Basic token auth here",
        "basic access_token_hint for docs",
        "line1\\nBearer token check",
        "password xyz-Bearer sample",
        "secret something-token",
        "Basic raw_transcript sample",
        "secret foo-Authorization",
        "secret something-api_key",
        "secret something-dataset_id",
        "secret something-document_id",
        "secret foo-private_locator",
    ],
)
def test_redact_public_ingress_does_not_synthesize_denylist_hits(benign_text: str) -> None:
    redacted = redact_public_ingress_text(benign_text)
    assert _denylist_hits(redacted) == []


def test_drain_enqueue_payload_passes_ingress_denylist_for_self_block_cases(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    source.write_text(
        "\n".join(
            [
                "user: explain Basic token vs Bearer token",
                "assistant: avoid \\nBearer token and password xyz-Bearer",
                "assistant: also secret something-token and Basic raw_transcript",
            ]
        ),
        encoding="utf-8",
    )
    request = {
        "schema_version": "agent_knowledge_capture_request.v1",
        "request_id": "req_self_block_repro",
        "provider": "claude",
        "project": "dendrite",
        "event_type": "session_end",
        "session_id_hash": sha256("claude:self-block-repro"),
        "observed_at": "2026-11-17T08:00:00+00:00",
        "source_locator": {
            "kind": "provider_transcript_source",
            "status": "source_locator_private_spool_only",
            "locator_hash": sha256(str(source)),
            "runtime_handle": str(source),
            "raw_path_present": True,
        },
        "redaction_version": "redaction.v2",
        "privacy_level": "private_session",
        "content_policy": "locator_only",
    }

    docs = build_drain_documents(request)
    assert len(docs) == 1
    body = build_ingress_enqueue_request_body(
        source=queue_source(request),
        packed=docs[0],
        content_hash=sha256(docs[0].body),
        target_profile="index-transcript-memory",
        kind=docs[0].kind,
        idempotency_key=f"claude:{docs[0].kind}:{sha256(docs[0].body)}",
    )
    serialized = json.dumps(body, sort_keys=True)
    assert _denylist_hits(body["payload"]["document"]["body"]) == []
    assert "Basic credential" not in serialized


def test_real_basic_credentials_are_redacted_and_raw_basic_remains_blocked_by_guard() -> None:
    raw_secret = "dXNlcjpwYXNzMTIzNDU2"
    header = f"Authorization: Basic {raw_secret}"
    standalone = f"Basic {raw_secret}"

    # Guard itself still catches unredacted Basic credentials.
    assert _denylist_hits(standalone) != []

    redacted_header = redact_public_ingress_text(header)
    redacted_standalone = redact_public_ingress_text(standalone)

    assert raw_secret not in redacted_header
    assert raw_secret not in redacted_standalone
    assert "<redacted:secret>" in redacted_header
    assert "<redacted:secret>" in redacted_standalone
    assert _denylist_hits(redacted_header) == []
    assert _denylist_hits(redacted_standalone) == []


def test_benign_korean_and_urls_pass_intact() -> None:
    sample = (
        "안녕하세요. 정상 한국어 대화와 참고 링크입니다: "
        "https://example.com/docs/v1/guide?lang=ko&tab=overview#top"
    )
    redacted = redact_public_ingress_text(sample)
    assert redacted == sample
    assert _denylist_hits(redacted) == []


def test_redact_public_ingress_text_is_idempotent() -> None:
    samples = [
        "use Basic token and \\nBearer token",
        "password xyz-Bearer sample and secret something-token",
        "Authorization: Basic dXNlcjpwYXNzMTIzNDU2",
        "Bearer abcdefghijklmnop",
        "path=/Users/example/work/app.py home=~/notes.md",
        "dataset_id=123 document_id=456 private_locator raw_transcript",
        "안녕하세요 https://example.com/a/b?x=1",
    ]
    for sample in samples:
        once = redact_public_ingress_text(sample)
        twice = redact_public_ingress_text(once)
        assert twice == once
        assert _denylist_hits(once) == []
