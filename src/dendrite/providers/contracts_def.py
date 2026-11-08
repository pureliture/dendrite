"""Source contract definition (ProviderSourceContract + 8 contracts)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSourceContract:
    contract_id: str
    provider: str
    provider_version: str
    installed_version_evidence: str
    hook_event: str
    source_locator_field: str
    parser_version: str
    native_parser_status: str
    privacy_redaction_status: str
    verification_status: str
    source_status: str
    hook_install_status: str
    rollback_state: str
    evidence_hash: str
    redacted_evidence_ref: str
    raw_prompt_policy: str = ""
    unsupported_reason: str = ""

    def to_record(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "installed_version_evidence": self.installed_version_evidence,
            "hook_event": self.hook_event,
            "source_locator_field": self.source_locator_field,
            "parser_version": self.parser_version,
            "native_parser_status": self.native_parser_status,
            "privacy_redaction_status": self.privacy_redaction_status,
            "verification_status": self.verification_status,
            "source_status": self.source_status,
            "hook_install_status": self.hook_install_status,
            "rollback_state": self.rollback_state,
            "evidence_hash": self.evidence_hash,
            "redacted_evidence_ref": self.redacted_evidence_ref,
            "raw_prompt_policy": self.raw_prompt_policy,
            "unsupported_reason": self.unsupported_reason,
        }


def build_default_provider_source_contracts() -> list[ProviderSourceContract]:
    from .registry import supported_names

    contracts: list[ProviderSourceContract] = [
        _claude_contract(),
        _codex_contract(),
        _gemini_contract(),
        _antigravity_contract(),
        _hermes_contract(),
        _grok_contract(),
        _opencode_contract(),
    ]
    if "omp" in set(supported_names()):
        contracts.append(_omp_contract())
    return contracts


def provider_source_contract_from_record(record: dict) -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id=record["contract_id"],
        provider=record["provider"],
        provider_version=record["provider_version"],
        installed_version_evidence=record.get("installed_version_evidence") or _default_installed_version_evidence(record),
        hook_event=record.get("hook_event", ""),
        source_locator_field=record.get("source_locator_field", ""),
        parser_version=record.get("parser_version", ""),
        native_parser_status=record.get("native_parser_status") or _default_native_parser_status(record),
        privacy_redaction_status=record.get("privacy_redaction_status") or _default_privacy_redaction_status(record),
        verification_status=record["verification_status"],
        source_status=record["source_status"],
        hook_install_status=record["hook_install_status"],
        rollback_state=record.get("rollback_state") or _default_rollback_state(record),
        evidence_hash=record["evidence_hash"],
        redacted_evidence_ref=record.get("redacted_evidence_ref") or "docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        raw_prompt_policy=record.get("raw_prompt_policy", ""),
        unsupported_reason=record.get("unsupported_reason", ""),
    )


def _evidence_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _claude_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="claude-code-stop-transcript-path.v1",
        provider="claude",
        provider_version="2.1.140",
        installed_version_evidence="2026-05-13_source_locator_smoke:claude-code 2.1.140",
        hook_event="Stop",
        source_locator_field="transcript_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_verified_claude_jsonl",
        privacy_redaction_status="privacy_redaction_verified_locator_only",
        verification_status="source_locator_verified_current_smoke",
        source_status="source_locator_verified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash=_evidence_hash({
            "provider": "claude", "provider_version": "2.1.140", "hook_event": "Stop",
            "source_locator_field": "transcript_path",
            "locator_hash": "sha256:011c885b8de1839868986379de349a37648401a0cb15d2cfce29145efaf95c61",
        }),
        redacted_evidence_ref="docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        raw_prompt_policy="locator_only_not_transcript_content",
    )


def _codex_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="codex-stop-transcript-path.v1",
        provider="codex",
        provider_version="codex-cli 0.130.0",
        installed_version_evidence="2026-05-13_source_locator_smoke:codex-cli 0.130.0",
        hook_event="Stop",
        source_locator_field="transcript_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_verified_codex_jsonl",
        privacy_redaction_status="privacy_redaction_verified_locator_only",
        verification_status="source_locator_verified_current_smoke",
        source_status="source_locator_verified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash=_evidence_hash({"provider": "codex", "provider_version": "codex-cli 0.130.0"}),
        redacted_evidence_ref="docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        raw_prompt_policy="locator_only_not_transcript_content",
    )


def _gemini_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="gemini-session-end-transcript-path.v1",
        provider="gemini",
        provider_version="gemini-cli 1.0.0",
        installed_version_evidence="2026-05-13_source_locator_smoke:gemini-cli 1.0.0",
        hook_event="SessionEnd",
        source_locator_field="transcript_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_verified_gemini_jsonl",
        privacy_redaction_status="privacy_redaction_verified_locator_only",
        verification_status="source_locator_verified_current_smoke",
        source_status="source_locator_verified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash=_evidence_hash({"provider": "gemini"}),
        redacted_evidence_ref="docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        raw_prompt_policy="locator_only_not_transcript_content",
    )


def _antigravity_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="antigravity-session-end-transcript-path.v1",
        provider="antigravity",
        provider_version="antigravity-cli 1.0.0",
        installed_version_evidence="2026-05-13_source_locator_smoke:antigravity-cli 1.0.0",
        hook_event="SessionEnd",
        source_locator_field="transcript_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_verified_antigravity_jsonl",
        privacy_redaction_status="privacy_redaction_verified_locator_only",
        verification_status="source_locator_verified_current_smoke",
        source_status="source_locator_verified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash=_evidence_hash({"provider": "antigravity"}),
        redacted_evidence_ref="docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        raw_prompt_policy="locator_only_not_transcript_content",
    )


def _hermes_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="hermes-session-end-state-db.v1",
        provider="hermes",
        provider_version="hermes 1.0.0",
        installed_version_evidence="pending_probe",
        hook_event="SessionEnd",
        source_locator_field="state_db_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_unverified_hermes_sqlite",
        privacy_redaction_status="privacy_redaction_unverified",
        verification_status="source_locator_unverified",
        source_status="source_locator_unverified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash="pending_operator_source_smoke",
        redacted_evidence_ref="docs/HERMES_PROVIDER.md",
        raw_prompt_policy="locator_only_not_transcript_content",
        unsupported_reason=(
            "hermes uses a shell hook (~/.hermes/config.yaml, session-end event) and a "
            "single SQLite session store (~/.hermes/state.db); dendrite reads that store "
            "read-only via a source adapter and ships a conversation_chunk. parser/source "
            "not yet live-verified against a real hermes install."
        ),
    )


def _grok_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="grok-stop-updates-jsonl.v1",
        provider="grok",
        provider_version="0.2.93",
        installed_version_evidence="2026-07-09_research_gate:grok 0.2.93",
        hook_event="Stop",
        source_locator_field="transcriptPath",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_unverified_grok_updates_jsonl",
        privacy_redaction_status="privacy_redaction_unverified",
        verification_status="source_locator_unverified",
        source_status="source_locator_unverified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash="pending_operator_source_smoke",
        redacted_evidence_ref="docs/live/grok-build-research-gate.md",
        raw_prompt_policy="locator_only_not_transcript_content",
        unsupported_reason=(
            "grok Build stores sessions under GROK_HOME/sessions/<cwd>/<id>/updates.jsonl; "
            "live Stop hook provides transcriptPath. dendrite ships opaque conversation_chunk. "
            "source contract not yet operator-verified for hook install."
        ),
    )


def _opencode_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="opencode-session-idle-session-db.v1",
        provider="opencode",
        provider_version="opencode 2.1.2",
        installed_version_evidence="pending_probe",
        hook_event="session.idle",
        source_locator_field="session_db_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_unverified_opencode_sqlite",
        privacy_redaction_status="privacy_redaction_unverified",
        verification_status="source_locator_unverified",
        source_status="source_locator_unverified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash="pending_operator_source_smoke",
        redacted_evidence_ref="docs/OPENCODE_PROVIDER.md",
        raw_prompt_policy="locator_only_not_transcript_content",
        unsupported_reason=(
            "opencode stores sessions in a single SQLite store "
            "(~/.local/share/opencode/opencode.db, session_v2 + session_message); "
            "live session.idle plugin provides sessionID. dendrite reads that store "
            "read-only via a source adapter and ships a conversation_chunk. "
            "source contract not yet operator-verified for hook install."
        ),
    )


def _omp_contract() -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id="omp-agent-end-session-jsonl.v1",
        provider="omp",
        provider_version="omp 18.16.0",
        installed_version_evidence="pending_probe",
        hook_event="agent_end",
        source_locator_field="transcript_path",
        parser_version="provider-transcript-parser.v1",
        native_parser_status="native_parser_unverified_omp_jsonl",
        privacy_redaction_status="privacy_redaction_unverified",
        verification_status="source_locator_unverified",
        source_status="source_locator_unverified",
        hook_install_status="deferred_not_installed",
        rollback_state="not_installed_no_runtime_rollback_needed",
        evidence_hash="pending_operator_source_smoke",
        redacted_evidence_ref="docs/OMP_PROVIDER.md",
        raw_prompt_policy="locator_only_not_transcript_content",
        unsupported_reason=(
            "oh-my-pi (omp) stores sessions as jsonl files under the omp agent "
            "sessions directory; the hook payload carries the transcript path. "
            "dendrite ships a conversation_chunk built by the omp jsonl adapter. "
            "source contract not yet operator-verified for hook install."
        ),
    )


def _default_installed_version_evidence(record: dict) -> str:
    provider = record.get("provider", "")
    version = record.get("provider_version", "")
    if provider == "claude":
        return f"2026-05-13_source_locator_smoke:claude-code {version}"
    if provider == "codex":
        return f"2026-05-13_source_locator_smoke:{version}"
    if provider == "antigravity":
        return "pending_probe"
    return f"2026-05-13_source_locator_smoke:{provider} {version}"


def _default_native_parser_status(record: dict) -> str:
    if record.get("provider") == "claude" and record.get("parser_version") == "provider-transcript-parser.v1":
        return "native_parser_verified_claude_jsonl"
    if record.get("hook_install_status") == "blocked_native_parser_unverified":
        return "blocked_native_parser_unverified"
    return "native_parser_unclassified"


def _default_privacy_redaction_status(record: dict) -> str:
    if record.get("verification_status") == "source_locator_verified_current_smoke":
        return "privacy_redaction_verified_locator_only"
    return "privacy_redaction_unverified"


def _default_rollback_state(record: dict) -> str:
    if record.get("hook_install_status") in {"deferred_not_installed", "blocked_native_parser_unverified"}:
        return "not_installed_no_runtime_rollback_needed"
    return "rollback_plan_required_before_mutation"
