from __future__ import annotations

import hashlib
import json

SAFE_PAYLOAD_FIELDS = {
    "cwd",
    "dedupe_key",
    "event_id",
    "event_type",
    "hook_event_name",
    "message",
    "observed_at",
    "privacy_level",
    "project",
    "reason",
    "session_id",
    "summary",
    "text",
    "turn_id",
}


def no_op_hook_response(provider: str) -> str:
    if provider not in {"codex", "claude", "gemini", "openclaw", "antigravity", "hermes"}:
        raise ValueError(f"unsupported provider: {provider}")
    return ""


def normalize_provider_event(provider: str, payload: dict) -> dict:
    if provider not in {"codex", "claude", "gemini", "openclaw", "antigravity", "hermes"}:
        raise ValueError(f"unsupported provider: {provider}")
    normalized = {key: value for key, value in payload.items() if key in SAFE_PAYLOAD_FIELDS}
    normalized["provider"] = provider
    if provider == "codex":
        _normalize_codex_hook_event(normalized, payload)
    elif provider == "claude":
        _normalize_claude_hook_event(normalized, payload)
    elif provider == "gemini":
        _normalize_gemini_hook_event(normalized, payload)
    elif provider == "antigravity":
        _normalize_antigravity_hook_event(normalized, payload)
    elif provider == "hermes":
        _normalize_hermes_hook_event(normalized, payload)
    if "prompt" in payload:
        normalized["prompt_hash"] = _hash_value(payload["prompt"])
    for output_key, candidates in {
        "tool_input_hash": ("tool_input", "raw_tool_input"),
        "tool_output_hash": ("tool_output", "raw_tool_output"),
    }.items():
        for candidate in candidates:
            if candidate in payload:
                normalized[output_key] = _hash_value(payload[candidate])
                break
    return normalized


def _normalize_codex_hook_event(normalized: dict, payload: dict) -> None:
    hook_event_name = payload.get("hook_event_name")
    if hook_event_name == "Stop":
        _set_lifecycle_event(
            normalized,
            provider="codex",
            hook_event_name=hook_event_name,
            session_id=payload.get("session_id", ""),
            event_type="session_end",
            reason=str(payload.get("reason", "")),
        )
    elif hook_event_name == "SessionStart":
        _set_lifecycle_event(
            normalized,
            provider="codex",
            hook_event_name=hook_event_name,
            session_id=payload.get("session_id", ""),
            event_type="session_start",
            reason="",
        )
    elif hook_event_name == "UserPromptSubmit":
        normalized["event_type"] = "user_prompt_seen"
    elif hook_event_name in {"PreToolUse", "PostToolUse", "PermissionRequest"}:
        normalized["event_type"] = "tool_use_summary"
        normalized.setdefault("summary", f"Codex {hook_event_name} hook observed without tool flow mutation.")


def _normalize_claude_hook_event(normalized: dict, payload: dict) -> None:
    hook_event_name = payload.get("hook_event_name")
    if hook_event_name in {"Stop", "SessionEnd"}:
        _set_lifecycle_event(
            normalized,
            provider="claude",
            hook_event_name=hook_event_name,
            session_id=payload.get("session_id", ""),
            event_type="session_end",
            reason=str(payload.get("reason", "")),
        )
    elif hook_event_name == "SessionStart":
        _set_lifecycle_event(
            normalized,
            provider="claude",
            hook_event_name=hook_event_name,
            session_id=payload.get("session_id", ""),
            event_type="session_start",
            reason="",
        )


def _normalize_gemini_hook_event(normalized: dict, payload: dict) -> None:
    hook_event_name = payload.get("hook_event_name")
    if hook_event_name == "SessionEnd":
        _set_lifecycle_event(
            normalized,
            provider="gemini",
            hook_event_name=hook_event_name,
            session_id=payload.get("session_id", ""),
            event_type="session_end",
            reason=str(payload.get("reason", "")),
        )
    elif hook_event_name == "SessionStart":
        _set_lifecycle_event(
            normalized,
            provider="gemini",
            hook_event_name=hook_event_name,
            session_id=payload.get("session_id", ""),
            event_type="session_start",
            reason="",
        )


def _normalize_antigravity_hook_event(normalized: dict, payload: dict) -> None:
    hook_event_name = str(payload.get("hook_event_name") or "Stop")
    if hook_event_name in {"Stop", "SessionEnd"} or payload.get("fullyIdle") is True or payload.get("terminationReason"):
        _set_lifecycle_event(
            normalized,
            provider="antigravity",
            hook_event_name="Stop",
            session_id=str(payload.get("conversationId") or payload.get("conversation_id") or ""),
            event_type="session_end",
            reason=str(payload.get("terminationReason") or payload.get("reason") or ""),
        )


def _normalize_hermes_hook_event(normalized: dict, payload: dict) -> None:
    hook_event_name = str(payload.get("hook_event_name") or "Stop")
    if hook_event_name in {"Stop", "SessionEnd", "session_end", "session:end", "on_session_end", "on_session_finalize"}:
        _set_lifecycle_event(
            normalized,
            provider="hermes",
            hook_event_name="Stop",
            session_id=str(payload.get("session_id") or ""),
            event_type="session_end",
            reason=str(payload.get("reason") or ""),
        )
    elif hook_event_name in {"SessionStart", "session_start", "session:start", "on_session_start"}:
        _set_lifecycle_event(
            normalized,
            provider="hermes",
            hook_event_name="SessionStart",
            session_id=str(payload.get("session_id") or ""),
            event_type="session_start",
            reason="",
        )


def _set_lifecycle_event(
    normalized: dict,
    *,
    provider: str,
    hook_event_name: str,
    session_id: str,
    event_type: str,
    reason: str,
) -> None:
    dedupe_key = ":".join([provider, hook_event_name, session_id, reason])
    normalized["event_type"] = event_type
    normalized["summary"] = f"{provider.title()} {hook_event_name} hook observed without prompt, context, tool, or model mutation."
    normalized["dedupe_key"] = dedupe_key
    normalized["event_id"] = "evt_" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:16]


def _hash_value(value) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
