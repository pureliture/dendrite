"""Drain shared helpers: error classification, hashing, formatting."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

RECOVERABLE_ERROR_CLASSES = {
    "ingress_invalid_json",
    "ingress_unreachable",
    "delivery_state_unavailable",
}


def classify_error(exc: Exception) -> str:
    message = str(exc)
    for marker in ("delivery_state_unavailable", "delivery_snapshot_invalid", "delivery_snapshot_missing",
                   "delivery_progress_invalid", "delivery_target_mismatch"):
        if marker in message:
            return marker
    for marker in ("source_unreadable", "source_unproven", "source_policy_blocked", "source_parse_failed"):
        if marker in message:
            return marker
    if "ingress enqueue" in message:
        if "unreachable" in message:
            return "ingress_unreachable"
        if "invalid_json" in message:
            return "ingress_invalid_json"
        return "ingress_rejected"
    if isinstance(exc, OSError):
        return "delivery_state_unavailable"
    return exc.__class__.__name__


# The ingress transport builds these messages from a closed vocabulary: HTTP
# status codes, the server's whitelisted rejection status, and an exception
# class name. Never transcript content. The detail is kept in ``message`` so a
# quarantined request records why the ingress refused it; anything that does
# not parse as that closed grammar collapses back to the error class alone.
_INGRESS_DETAIL_RE = re.compile(r"^ingress enqueue (?:rejected|failed): (.{1,120})$")
_SAFE_DETAIL_RE = re.compile(
    r"^(?:status=[a-z_]{1,32}(?: error=[A-Za-z0-9_]{1,64})?"
    r"|unreachable (?:http_[0-9]{3}|[a-z0-9_]{1,64}))$"
)


def ingress_failure_detail(message: str | None) -> str:
    match = _INGRESS_DETAIL_RE.match(" ".join(str(message or "").split()))
    if match is None:
        return ""
    detail = match.group(1).strip()
    if not detail or not _SAFE_DETAIL_RE.match(detail):
        return ""
    return detail


def failure_message(error_class: str, message: str | None = None) -> str:
    detail = ingress_failure_detail(message) if error_class.startswith("ingress_") else ""
    return f"{error_class}; {detail}" if detail else error_class


def failure_record(
    error_class: str, request_path: Path | str | None = None, *, message: str | None = None
) -> dict:
    retry_attempts = 1
    if request_path is not None:
        try:
            request = json.loads(Path(request_path).read_text(encoding="utf-8"))
            last_failure = request.get("last_failure") or {}
            retry_attempts = int(last_failure.get("retry_attempts") or 0) + 1
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            retry_attempts = 1
    return {
        "error_class": error_class,
        "message": failure_message(error_class, message),
        "recoverable": error_class in RECOVERABLE_ERROR_CLASSES,
        "retry_attempts": retry_attempts,
        "last_attempt_at": now_iso(),
    }


def is_recoverable_error_class(error_class: str) -> bool:
    return error_class in RECOVERABLE_ERROR_CLASSES


def slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in str(value))
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "unknown"


def hash_fragment(value: str, length: int) -> str:
    if ":" in value:
        value = value.split(":", 1)[1]
    value = "".join(char for char in value if char.lower() in "0123456789abcdef")
    if len(value) >= length:
        return value[:length].lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_utc_timestamp(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return slug(value)[:32] or "unknown-time"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
