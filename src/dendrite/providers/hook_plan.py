"""Non-mutating provider hook-plan generation."""
from __future__ import annotations

import shlex
from pathlib import Path

from ..hermes_profiles import build_hermes_profile_readiness
from .contracts_def import build_default_provider_source_contracts
from .registry import supported_names


def build_provider_hook_plan(*, provider: str, action: str, dendrite_command="dendrite", project="<project>", capture_spool="<private-transcript-capture-spool>", hermes_home=None) -> dict:
    supported = set(supported_names())
    if provider not in supported:
        raise ValueError(f"unsupported provider: {provider}")
    if action not in {"install", "uninstall"}:
        raise ValueError(f"unsupported hook action: {action}")
    contract = _contract_for(provider)
    profile_readiness = build_hermes_profile_readiness(hermes_home) if provider == "hermes" else {}
    approval_required_fields = ["exact_argv", "timeout_seconds", "redaction_required", "abort_criteria", "rollback_owner", "rollback_steps"]
    if action == "install" and contract.source_status != "source_locator_verified":
        return {
            "schema_version": "agent_knowledge_provider_hook_plan.v1",
            "provider": provider, "action": action, "planned_status": "blocked_source_unproven",
            "network_used": False, "live_mutation_allowed": False,
            "hook_mutation_performed": False, "mutation_performed": False,
            "exact_target": _exact_target(contract), "planned_argv": [],
            "contract": contract.to_record(), "profile_readiness": profile_readiness,
            "requires_approval_before_execution": True, "approval_required_fields": approval_required_fields,
            "blocker": {"reason": "source locator contract is not verified", "required_before_install": "approved provider-native source capture smoke with exact argv and redacted evidence"},
            "rollback": {"owner": "operator", "steps": [f"dendrite provider hook-plan --provider {provider} --action uninstall", "Do not install provider hooks until the source contract is verified."]},
            **_provider_config_plan(contract, action=action, dendrite_command=dendrite_command, project=project, capture_spool=capture_spool),
        }
    if action == "install" and contract.hook_install_status == "blocked_native_parser_unverified":
        return {
            "schema_version": "agent_knowledge_provider_hook_plan.v1",
            "provider": provider, "action": action, "planned_status": "blocked_native_parser_unverified",
            "network_used": False, "live_mutation_allowed": False,
            "hook_mutation_performed": False, "mutation_performed": False,
            "exact_target": _exact_target(contract), "planned_argv": [],
            "contract": contract.to_record(), "profile_readiness": profile_readiness,
            "requires_approval_before_execution": True, "approval_required_fields": approval_required_fields,
            "blocker": {"reason": "provider-native transcript parser is not verified", "required_before_install": "approved provider-native parser smoke or an explicit fixture-export-only capture path"},
            "rollback": {"owner": "operator", "steps": [f"dendrite provider hook-plan --provider {provider} --action uninstall", "Do not install provider hooks until native parser readiness is approved."]},
            **_provider_config_plan(contract, action=action, dendrite_command=dendrite_command, project=project, capture_spool=capture_spool),
        }
    return {
        "schema_version": "agent_knowledge_provider_hook_plan.v1",
        "provider": provider, "action": action, "planned_status": "plan_only",
        "network_used": False, "live_mutation_allowed": False,
        "hook_mutation_performed": False, "mutation_performed": False,
        "exact_target": _exact_target(contract),
        "planned_argv": _planned_argv(contract, dendrite_command=dendrite_command, project=project, capture_spool=capture_spool),
        "contract": contract.to_record(), "profile_readiness": profile_readiness,
        "requires_approval_before_execution": True, "approval_required_fields": approval_required_fields,
        **_provider_config_plan(contract, action=action, dendrite_command=dendrite_command, project=project, capture_spool=capture_spool),
        "rollback": {"owner": "operator", "steps": [f"dendrite provider hook-plan --provider {provider} --action {_opposite_action(action)}", "Review the generated non-mutating plan before any future provider config change."]},
    }


def _contract_for(provider: str):
    for contract in build_default_provider_source_contracts():
        if contract.provider == provider:
            return contract
    raise ValueError(f"unsupported provider: {provider}")


def _exact_target(contract) -> str:
    return f"{contract.provider} {contract.hook_event or 'unproven'} hook transcript-capture source locator"


def _planned_argv(contract, *, dendrite_command="dendrite", project="<project>", capture_spool="<private-transcript-capture-spool>"):
    return [dendrite_command, "transcript-capture", "--provider", contract.provider, "--project", project, "--spool", capture_spool, "--stdin-json", "--non-fatal"]


def _shell_join(argv) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv)


def _opposite_action(action: str) -> str:
    return "uninstall" if action == "install" else "install"


def _provider_config_plan(contract, *, action, dendrite_command, project, capture_spool):
    if contract.provider not in {"claude", "antigravity", "grok", "opencode", "omp"}:
        return {}
    planned_argv = _planned_argv(contract, dendrite_command=dendrite_command, project=project, capture_spool=capture_spool)
    if contract.provider == "omp":
        return {"provider_config": {"schema_version": "agent_knowledge_omp_hook_plan.v1", "provider": "omp", "action": action, "config_format": "omp_extension_ts", "config_scope": "omp_agent_extensions", "candidate_write_targets": ["~/.omp/agent/extensions/dendrite-capture.ts"], "event": contract.hook_event or "agent_end", "managed_entry": {"event": contract.hook_event or "agent_end", "plugin": "dendrite-capture.ts", "hooks": [{"type": "command", "command": _shell_join(planned_argv), "timeout": 10}]}, "merge_strategy": "add_extension_file_without_rewriting_existing_extensions", "install_performed": False, "provider_config_mutation_performed": False, "postcheck": ["extension file parses as a valid omp extension plugin", "agent_end handler command matches the approved transcript-capture argv", "hook writes locator-only capture requests to the private capture spool", "no backend credential or transcript body is present in the extension entry"]}}
    if contract.provider == "claude":
        return {"provider_config": {"schema_version": "agent_knowledge_claude_code_hook_plan.v1", "provider": "claude", "action": action, "config_format": "claude_settings_json", "config_scope": "claude_code_settings", "candidate_write_targets": ["~/.claude/settings.json", "<project>/.claude/settings.json"], "event": contract.hook_event, "managed_entry": {"matcher": "", "hooks": [{"type": "command", "command": _shell_join(planned_argv)}]}, "merge_strategy": "append_only_to_hooks_Stop_without_rewriting_existing_entries", "install_performed": False, "provider_config_mutation_performed": False, "postcheck": ["settings JSON parses after adding the managed Stop entry", "Stop hook command matches the approved transcript-capture argv", "hook writes locator-only capture requests to the private capture spool", "no backend credential or transcript body is present in the hook entry"]}}
    if contract.provider == "antigravity":
        return {"provider_config": {"schema_version": "agent_knowledge_antigravity_hook_plan.v1", "provider": "antigravity", "action": action, "config_format": "antigravity_hooks_json", "config_scope": "antigravity_agent_hooks", "candidate_write_targets": ["~/.gemini/config/hooks.json", "<project>/.agents/hooks.json"], "event": contract.hook_event, "managed_entry": {"type": "command", "command": _shell_join(planned_argv)}, "merge_strategy": f"append_only_to_hooks_{contract.hook_event}_without_rewriting_existing_entries", "install_performed": False, "provider_config_mutation_performed": False, "postcheck": ["hooks.json parses after adding the managed entry", f"{contract.hook_event} hook command matches the approved transcript-capture argv", "hook writes locator-only capture requests to the private capture spool", "no backend credential or transcript body is present in the hook entry"]}}
    if contract.provider == "grok":
        return {"provider_config": {"schema_version": "agent_knowledge_grok_build_hook_plan.v1", "provider": "grok", "action": action, "config_format": "grok_hooks_json", "config_scope": "grok_global_hooks", "candidate_write_targets": ["~/.grok/hooks/*.json", "<project>/.grok/hooks/*.json"], "event": contract.hook_event or "Stop", "managed_entry": {"hooks": {(contract.hook_event or "Stop"): [{"hooks": [{"type": "command", "command": _shell_join(planned_argv), "timeout": 10}]}]}}, "merge_strategy": "append_only_to_hooks_Stop_without_rewriting_existing_entries", "install_performed": False, "provider_config_mutation_performed": False, "postcheck": ["hook JSON under ~/.grok/hooks/ parses after adding the managed Stop entry", "Stop hook command matches the approved transcript-capture argv", "hook writes locator-only capture requests to the private capture spool", "no backend credential or transcript body is present in the hook entry"]}}
    # opencode
    return {"provider_config": {"schema_version": "agent_knowledge_opencode_plugin_plan.v1", "provider": "opencode", "action": action, "config_format": "opencode_plugin_ts", "config_scope": "opencode_config_plugins", "candidate_write_targets": ["~/.config/opencode/plugins/dendrite-capture.ts", "~/.config/opencode/opencode.json"], "event": contract.hook_event or "session.idle", "managed_entry": {"event": contract.hook_event or "session.idle", "plugin": "dendrite-capture.ts", "hooks": [{"type": "plugin", "command": _shell_join(planned_argv), "timeout": 10}]}, "merge_strategy": "add_plugin_file_without_rewriting_existing_plugins", "install_performed": False, "provider_config_mutation_performed": False, "postcheck": ["plugin file parses as a valid opencode event plugin", "session.idle handler command matches the approved transcript-capture argv", "hook writes locator-only capture requests to the private capture spool", "no backend credential or transcript body is present in the plugin entry"]}}
