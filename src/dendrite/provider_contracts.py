"""Backwards-compatible re-export surface for `dendrite.provider_contracts`.

Implementation moved to:
- `dendrite.providers.contracts_def` (contract definitions)
- `dendrite.providers.doctor` (doctor/parser matrix)
- `dendrite.providers.hook_plan` (hook plan)
"""
from __future__ import annotations

from .providers.contracts_def import (
    ProviderSourceContract,
    build_default_provider_source_contracts,
    provider_source_contract_from_record,
)
from .providers.doctor import build_provider_doctor_report, build_provider_parser_matrix
from .providers.hook_plan import build_provider_hook_plan
from .providers.registry import supported_names

SUPPORTED_PROVIDERS = frozenset(supported_names())
SUPPORTED_HOOK_ACTIONS = frozenset({"install", "uninstall"})

__all__ = [
    "ProviderSourceContract",
    "SUPPORTED_PROVIDERS",
    "SUPPORTED_HOOK_ACTIONS",
    "build_default_provider_source_contracts",
    "build_provider_doctor_report",
    "build_provider_parser_matrix",
    "build_provider_hook_plan",
    "provider_source_contract_from_record",
]
