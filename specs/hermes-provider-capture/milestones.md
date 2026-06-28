# Milestones — hermes-provider-capture

## M0 Red tests
- status: done
- evidence: 14 new tests red before impl (hermes unregistered; CLI rejected --provider hermes)

## M1 Provider identity
- status: done
- evidence: hermes in SUPPORTED_PROVIDERS / SUPPORTED_TRANSCRIPT_PROVIDERS / no_op_hook_response /
  normalize_provider_event / contract list / CLI choices; providers/hermes.py stub. Identity tests green.

## M2 Locator + detector + capture
- status: done
- evidence: _resolve_hermes_session_locator (explicit keys + HERMES_HOME + default, existence+symlink
  guard, never opens SQLite); .hermes storage-path detection. Locator-only capture / no-source / raw
  reject / no-store-read tests green. CLI smoke: spooled, locator_only, hashes only, no body sentinel.

## M3 Drain pointer ship + regression
- status: done
- evidence: build_drain_document hermes branch -> _build_hermes_pointer_document (no body read);
  ships kind=session_pointer with content_kind=locator_pointer. Pointer-ship test green (body+metadata
  byte-clean of store path/body/session id); codex body-pack regression green.

## M4 CLI + hook-plan deferred + docs/sample
- status: done
- evidence: CLI --provider hermes reachable (hook-plan + transcript-capture); hook-plan -> blocked_source_unproven,
  mutation_performed False. docs/HERMES_PROVIDER.md (enable/safety/sample) + README Providers badge/note.

## M5 Full local verification
- status: done
- evidence: `uv run pytest -q` 119 passed. boundary tests green. --show-boundary intact. CLI capture smoke
  no raw path/body leak. Adversarial opus review: all 6 boundary/regression items PASS; 3 findings (D1 design-invariant,
  D2/D3 test strength) fixed and re-verified (+2 lock tests, now 16 hermes tests / 119 total).
