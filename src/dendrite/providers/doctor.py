"""Doctor / parser-matrix reporting."""
from __future__ import annotations

from pathlib import Path

from ..hermes_profiles import build_hermes_profile_readiness


def build_provider_doctor_report(contracts=None, *, contract_source="committed_defaults", seeded_defaults=False, hermes_home=None) -> dict:
    from .contracts_def import build_default_provider_source_contracts

    selected = build_default_provider_source_contracts() if contracts is None else contracts
    return {
        "schema_version": "agent_knowledge_provider_doctor.v1",
        "contract_source": contract_source,
        "seeded_defaults": seeded_defaults,
        "network_used": False,
        "live_mutation_allowed": False,
        "hook_mutation_performed": False,
        "mutation_performed": False,
        "summary": {c.provider: c.verification_status for c in selected},
        "provider_parser_matrix": build_provider_parser_matrix(selected),
        "provider_profile_readiness": {"hermes": build_hermes_profile_readiness(hermes_home)},
        "providers": {c.provider: c.to_record() for c in selected},
    }


def build_provider_parser_matrix(contracts) -> dict:
    return {c.provider: _row(c) for c in contracts}


def _row(contract) -> dict:
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
