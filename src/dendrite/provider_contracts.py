from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import shlex


SUPPORTED_PROVIDERS = {"claude", "gemini", "codex", "antigravity", "hermes"}
SUPPORTED_HOOK_ACTIONS = {"install", "uninstall"}


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
    return [
        ProviderSourceContract(
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
            evidence_hash=_evidence_hash(
                {
                    "provider": "claude",
                    "provider_version": "2.1.140",
                    "hook_event": "Stop",
                    "source_locator_field": "transcript_path",
                    "locator_hash": "sha256:011c885b8de1839868986379de349a37648401a0cb15d2cfce29145efaf95c61",
                    "evidence": [
                        "docs/live/2026-05-13-provider-source-smoke-execution-results.md",
                    ],
                }
            ),
            redacted_evidence_ref="docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        ),
        ProviderSourceContract(
            contract_id="gemini-session-end-transcript-path.v1",
            provider="gemini",
            provider_version="0.41.2",
            installed_version_evidence="2026-05-15_native_parser_smoke:gemini 0.41.2",
            hook_event="SessionEnd",
            source_locator_field="transcript_path",
            parser_version="provider-transcript-parser.v1",
            native_parser_status="native_parser_verified_gemini_jsonl",
            privacy_redaction_status="privacy_redaction_verified_locator_only",
            verification_status="source_locator_verified_current_smoke",
            source_status="source_locator_verified",
            hook_install_status="deferred_not_installed",
            rollback_state="not_installed_no_runtime_rollback_needed",
            evidence_hash=_evidence_hash(
                {
                    "provider": "gemini",
                    "provider_version": "0.41.2",
                    "hook_event": "SessionEnd",
                    "source_locator_field": "transcript_path",
                    "parser_version": "provider-transcript-parser.v1",
                    "native_parser_status": "native_parser_verified_gemini_jsonl",
                    "locator_hash": "sha256:bb7763569470e74afd3df723b722025af8cb169fa1c5857a37d60a9b026cbbf0",
                    "evidence": [
                        "docs/live/2026-05-13-provider-source-smoke-execution-results.md",
                        "docs/live/2026-05-15-provider-native-parser-smoke-results.md",
                    ],
                }
            ),
            redacted_evidence_ref="docs/live/2026-05-15-provider-native-parser-smoke-results.md",
        ),
        ProviderSourceContract(
            contract_id="codex-stop-transcript-path.v1",
            provider="codex",
            provider_version="codex-cli 0.130.0",
            installed_version_evidence="2026-05-15_native_parser_smoke:codex-cli 0.130.0",
            hook_event="Stop",
            source_locator_field="transcript_path",
            parser_version="provider-transcript-parser.v1",
            native_parser_status="native_parser_verified_codex_jsonl",
            privacy_redaction_status="privacy_redaction_verified_locator_only",
            verification_status="source_locator_verified_current_smoke",
            source_status="source_locator_verified",
            hook_install_status="deferred_not_installed",
            rollback_state="not_installed_no_runtime_rollback_needed",
            evidence_hash=_evidence_hash(
                {
                    "provider": "codex",
                    "provider_version": "codex-cli 0.130.0",
                    "hook_event": "Stop",
                    "source_locator_field": "transcript_path",
                    "parser_version": "provider-transcript-parser.v1",
                    "native_parser_status": "native_parser_verified_codex_jsonl",
                    "locator_hash": "sha256:7fa797c3e010b9e062a18ab6b511ab26330f67054bcfb2f4925963113364747a",
                    "raw_prompt_policy": "locator_only_not_transcript_content",
                    "evidence": [
                        "docs/live/2026-05-13-provider-source-smoke-execution-results.md",
                        "docs/live/2026-05-15-provider-native-parser-smoke-results.md",
                    ],
                }
            ),
            redacted_evidence_ref="docs/live/2026-05-15-provider-native-parser-smoke-results.md",
            raw_prompt_policy="locator_only_not_transcript_content",
        ),
        ProviderSourceContract(
            contract_id="antigravity-session-end-transcript-path.v1",
            provider="antigravity",
            provider_version="Antigravity 2.0 (OpenClaw)",
            installed_version_evidence="pending_probe",
            hook_event="Stop",
            source_locator_field="transcript_path",
            parser_version="provider-transcript-parser.v1",
            native_parser_status="native_parser_verified_antigravity_jsonl",
            privacy_redaction_status="privacy_redaction_unverified",
            verification_status="source_locator_verified_current_smoke",
            source_status="source_locator_verified",
            hook_install_status="deferred_not_installed",
            rollback_state="not_installed_no_runtime_rollback_needed",
            evidence_hash="pending_probe",
            redacted_evidence_ref="",
        ),
        ProviderSourceContract(
            contract_id="hermes-session-end-state-db.v1",
            provider="hermes",
            provider_version="pending_probe",
            installed_version_evidence="pending_probe",
            hook_event="on_session_end",
            source_locator_field="session_db_path",
            parser_version="provider-transcript-parser.v1",
            native_parser_status="native_parser_unverified_hermes_sqlite",
            privacy_redaction_status="privacy_redaction_unverified",
            verification_status="source_locator_unverified",
            source_status="source_locator_unverified",
            hook_install_status="deferred_not_installed",
            rollback_state="not_installed_no_runtime_rollback_needed",
            evidence_hash="pending_probe",
            redacted_evidence_ref="",
            raw_prompt_policy="locator_only_not_transcript_content",
            unsupported_reason=(
                "hermes uses a shell hook (~/.hermes/config.yaml, session-end event) and a "
                "single SQLite session store (~/.hermes/state.db); dendrite reads that store "
                "read-only via a source adapter and ships a conversation_chunk. parser/source "
                "not yet live-verified against a real hermes install."
            ),
        ),
    ]


def build_provider_doctor_report(
    contracts: list[ProviderSourceContract] | None = None,
    *,
    contract_source: str = "committed_defaults",
    seeded_defaults: bool = False,
) -> dict:
    selected_contracts = build_default_provider_source_contracts() if contracts is None else contracts
    return {
        "schema_version": "agent_knowledge_provider_doctor.v1",
        "contract_source": contract_source,
        "seeded_defaults": seeded_defaults,
        "network_used": False,
        "live_mutation_allowed": False,
        "hook_mutation_performed": False,
        "mutation_performed": False,
        "summary": {contract.provider: contract.verification_status for contract in selected_contracts},
        "provider_parser_matrix": build_provider_parser_matrix(selected_contracts),
        "providers": {contract.provider: contract.to_record() for contract in selected_contracts},
    }


def build_provider_parser_matrix(contracts: list[ProviderSourceContract]) -> dict[str, dict]:
    return {contract.provider: _provider_parser_matrix_row(contract) for contract in contracts}


def provider_source_contract_from_record(record: dict) -> ProviderSourceContract:
    return ProviderSourceContract(
        contract_id=record["contract_id"],
        provider=record["provider"],
        provider_version=record["provider_version"],
        installed_version_evidence=record.get("installed_version_evidence")
        or _default_installed_version_evidence(record),
        hook_event=record.get("hook_event", ""),
        source_locator_field=record.get("source_locator_field", ""),
        parser_version=record.get("parser_version", ""),
        native_parser_status=record.get("native_parser_status") or _default_native_parser_status(record),
        privacy_redaction_status=record.get("privacy_redaction_status")
        or _default_privacy_redaction_status(record),
        verification_status=record["verification_status"],
        source_status=record["source_status"],
        hook_install_status=record["hook_install_status"],
        rollback_state=record.get("rollback_state") or _default_rollback_state(record),
        evidence_hash=record["evidence_hash"],
        redacted_evidence_ref=record.get("redacted_evidence_ref")
        or "docs/live/2026-05-13-provider-source-smoke-execution-results.md",
        raw_prompt_policy=record.get("raw_prompt_policy", ""),
        unsupported_reason=record.get("unsupported_reason", ""),
    )


def build_provider_hook_plan(
    *,
    provider: str,
    action: str,
    dendrite_command: str = "dendrite",
    project: str = "<project>",
    capture_spool: str = "<private-transcript-capture-spool>",
) -> dict:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if action not in SUPPORTED_HOOK_ACTIONS:
        raise ValueError(f"unsupported hook action: {action}")
    contract = _contract_for(provider)
    approval_required_fields = [
        "exact_argv",
        "timeout_seconds",
        "redaction_required",
        "abort_criteria",
        "rollback_owner",
        "rollback_steps",
    ]
    if action == "install" and contract.source_status != "source_locator_verified":
        return {
            "schema_version": "agent_knowledge_provider_hook_plan.v1",
            "provider": provider,
            "action": action,
            "planned_status": "blocked_source_unproven",
            "network_used": False,
            "live_mutation_allowed": False,
            "hook_mutation_performed": False,
            "mutation_performed": False,
            "exact_target": _exact_target(contract),
            "planned_argv": [],
            "contract": contract.to_record(),
            "requires_approval_before_execution": True,
            "approval_required_fields": approval_required_fields,
            "blocker": {
                "reason": "source locator contract is not verified",
                "required_before_install": (
                    "approved provider-native source capture smoke with exact argv and redacted evidence"
                ),
            },
            "rollback": {
                "owner": "operator",
                "steps": [
                    f"dendrite provider hook-plan --provider {provider} --action uninstall",
                    "Do not install provider hooks until the source contract is verified.",
                ],
            },
        }
    if action == "install" and contract.hook_install_status == "blocked_native_parser_unverified":
        return {
            "schema_version": "agent_knowledge_provider_hook_plan.v1",
            "provider": provider,
            "action": action,
            "planned_status": "blocked_native_parser_unverified",
            "network_used": False,
            "live_mutation_allowed": False,
            "hook_mutation_performed": False,
            "mutation_performed": False,
            "exact_target": _exact_target(contract),
            "planned_argv": [],
            "contract": contract.to_record(),
            "requires_approval_before_execution": True,
            "approval_required_fields": approval_required_fields,
            "blocker": {
                "reason": "provider-native transcript parser is not verified",
                "required_before_install": (
                    "approved provider-native parser smoke or an explicit fixture-export-only capture path"
                ),
            },
            "rollback": {
                "owner": "operator",
                "steps": [
                    f"dendrite provider hook-plan --provider {provider} --action uninstall",
                    "Do not install provider hooks until native parser readiness is approved.",
                ],
            },
        }
    return {
        "schema_version": "agent_knowledge_provider_hook_plan.v1",
        "provider": provider,
        "action": action,
        "planned_status": "plan_only",
        "network_used": False,
        "live_mutation_allowed": False,
        "hook_mutation_performed": False,
        "mutation_performed": False,
        "exact_target": _exact_target(contract),
        "planned_argv": _planned_argv(
            contract,
            dendrite_command=dendrite_command,
            project=project,
            capture_spool=capture_spool,
        ),
        "contract": contract.to_record(),
        "requires_approval_before_execution": True,
        "approval_required_fields": approval_required_fields,
        **_provider_config_plan(
            contract,
            action=action,
            dendrite_command=dendrite_command,
            project=project,
            capture_spool=capture_spool,
        ),
        "rollback": {
            "owner": "operator",
            "steps": [
                f"dendrite provider hook-plan --provider {provider} --action {_opposite_action(action)}",
                "Review the generated non-mutating plan before any future provider config change.",
            ],
        },
    }


def _contract_for(provider: str) -> ProviderSourceContract:
    for contract in build_default_provider_source_contracts():
        if contract.provider == provider:
            return contract
    raise ValueError(f"unsupported provider: {provider}")


def _provider_parser_matrix_row(contract: ProviderSourceContract) -> dict:
    return {
        "provider": contract.provider,
        "installed_version_evidence": contract.installed_version_evidence,
        "provider_version": contract.provider_version,
        "source_locator_status": contract.verification_status,
        "source_locator_field": contract.source_locator_field,
        "native_parser_status": contract.native_parser_status,
        "parser_version": contract.parser_version,
        "privacy_redaction_status": contract.privacy_redaction_status,
        "hook_install_status": contract.hook_install_status,
        "rollback_state": contract.rollback_state,
        "evidence_hash": contract.evidence_hash,
        "redacted_evidence_ref": contract.redacted_evidence_ref,
        "raw_prompt_policy": contract.raw_prompt_policy,
        "unsupported_reason": contract.unsupported_reason,
    }


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


def _exact_target(contract: ProviderSourceContract) -> str:
    hook_event = contract.hook_event or "unproven"
    return f"{contract.provider} {hook_event} hook transcript-capture source locator"


def _planned_argv(
    contract: ProviderSourceContract,
    *,
    dendrite_command: str = "dendrite",
    project: str = "<project>",
    capture_spool: str = "<private-transcript-capture-spool>",
) -> list[str]:
    return [
        dendrite_command,
        "transcript-capture",
        "--provider",
        contract.provider,
        "--project",
        project,
        "--spool",
        capture_spool,
        "--stdin-json",
        "--non-fatal",
    ]


def _provider_config_plan(
    contract: ProviderSourceContract,
    *,
    action: str,
    dendrite_command: str,
    project: str,
    capture_spool: str,
) -> dict:
    if contract.provider not in {"claude", "antigravity"}:
        return {}
    planned_argv = _planned_argv(
        contract,
        dendrite_command=dendrite_command,
        project=project,
        capture_spool=capture_spool,
    )
    if contract.provider == "claude":
        hook_entry = {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": _shell_join(planned_argv),
                }
            ],
        }
        return {
            "provider_config": {
                "schema_version": "agent_knowledge_claude_code_hook_plan.v1",
                "provider": "claude",
                "action": action,
                "config_format": "claude_settings_json",
                "config_scope": "claude_code_settings",
                "candidate_write_targets": [
                    "~/.claude/settings.json",
                    "<project>/.claude/settings.json",
                ],
                "event": contract.hook_event,
                "managed_entry": hook_entry,
                "merge_strategy": "append_only_to_hooks_Stop_without_rewriting_existing_entries",
                "install_performed": False,
                "provider_config_mutation_performed": False,
                "postcheck": [
                    "settings JSON parses after adding the managed Stop entry",
                    "Stop hook command matches the approved transcript-capture argv",
                    "hook writes locator-only capture requests to the private capture spool",
                    "no RAGFlow credential or transcript body is present in the hook entry",
                ],
            }
        }
    elif contract.provider == "antigravity":
        hook_entry = {
            "type": "command",
            "command": _shell_join(planned_argv),
        }
        return {
            "provider_config": {
                "schema_version": "agent_knowledge_antigravity_hook_plan.v1",
                "provider": "antigravity",
                "action": action,
                "config_format": "antigravity_hooks_json",
                "config_scope": "antigravity_agent_hooks",
                "candidate_write_targets": [
                    "~/.gemini/config/hooks.json",
                    "<project>/.agents/hooks.json",
                ],
                "event": contract.hook_event,
                "managed_entry": hook_entry,
                "merge_strategy": f"append_only_to_hooks_{contract.hook_event}_without_rewriting_existing_entries",
                "install_performed": False,
                "provider_config_mutation_performed": False,
                "postcheck": [
                    "hooks.json parses after adding the managed entry",
                    f"{contract.hook_event} hook command matches the approved transcript-capture argv",
                    "hook writes locator-only capture requests to the private capture spool",
                    "no RAGFlow credential or transcript body is present in the hook entry",
                ],
            }
        }


def _opposite_action(action: str) -> str:
    return "uninstall" if action == "install" else "install"


def _evidence_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv)
