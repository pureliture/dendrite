"""Single source of truth for provider identity metadata.

All 7 pre-existing providers (and new ones such as omp) register here.
Other modules derive their provider lists from this registry and never
hard-code a provider name list of their own. SOURCE_* error classes
are shared so the drain can classify adapter failures and quarantine
without leaking transcript content.

Boundary: provider hook -> locator-only spool/outbox -> thin shipper ->
POST 18080. This registry carries no backend/GC/session-memory logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AdapterKind = Literal["jsonl", "hermes-sqlite", "opencode-sqlite", "omp-jsonl"]

# Adapters the registry knows how to instantiate. Unknown kinds are
# rejected at import time by the _PROVIDERS table below via _build_registry.
ADAPTER_KINDS: frozenset[str] = frozenset(
    {"jsonl", "hermes-sqlite", "opencode-sqlite", "omp-jsonl"}
)

PROVIDER_NAMES: tuple[str, ...] = (
    "claude",
    "codex",
    "gemini",
    "antigravity",
    "grok",
)


@dataclass(frozen=True)
class ProviderEntry:
    """One provider's identity metadata.

    ``adapter_kind`` tells :mod:`dendrite.transcript_source` which
    adapter to instantiate. The remaining fields are metadata carried
    for diagnostics and hook planning; they are not behavioural knobs.
    """

    name: str
    adapter_kind: AdapterKind = "jsonl"


def _build_registry(names: tuple[str, ...]) -> dict[str, ProviderEntry]:
    """Build the registry, rejecting unknown adapter kinds at import time."""
    registry: dict[str, ProviderEntry] = {}
    for name in names:
        entry = ProviderEntry(name=name)
        if entry.adapter_kind not in ADAPTER_KINDS:
            raise ValueError(f"unknown adapter kind {entry.adapter_kind!r} for provider {name!r}")
        registry[name] = entry
    # Provider-specific adapter kinds are registered here so adding a
    # provider is a one-line change in this module.
    registry["hermes"] = ProviderEntry(name="hermes", adapter_kind="hermes-sqlite")
    registry["opencode"] = ProviderEntry(name="opencode", adapter_kind="opencode-sqlite")
    registry["omp"] = ProviderEntry(name="omp", adapter_kind="omp-jsonl")
    return registry


_PROVIDERS: dict[str, ProviderEntry] = _build_registry(PROVIDER_NAMES)


def get_registry() -> dict[str, ProviderEntry]:
    """Return the provider registry (name -> entry)."""
    return _PROVIDERS


def adapter_kind_for(provider: str) -> AdapterKind:
    """Return the adapter kind registered for ``provider``.

    Unknown providers raise :class:`ValueError` so the drain can
    classify the request as ``source_unproven`` and quarantine it
    instead of silently falling back to the jsonl adapter.
    """
    try:
        return _PROVIDERS[provider].adapter_kind
    except KeyError:
        raise ValueError("source_unproven") from None


def is_known_provider(provider: str) -> bool:
    return provider in _PROVIDERS


def supported_names() -> tuple[str, ...]:
    """Provider names in registry order."""
    return tuple(_PROVIDERS.keys())
