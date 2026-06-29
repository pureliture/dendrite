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

## M7 Adapter redesign (SoT regression after user direction B)
- status: done
- note: 사용자가 Hermes hooks 문서 제시 → "hook 미확인" 정정. 이어 "DB면 조회하면 되지 않나 +
  인터페이스 1개+adapter로 깔끔히" 방향(선택안 B) 선택. pointer/defer 폐기, neurons 후속 task 정리(dismiss).
- evidence: 신규 transcript_source.py(TranscriptSourceAdapter + Jsonl/HermesSqlite adapters + adapter_for).
  drain은 adapter로 provider-agnostic 복귀. Hermes는 state.db를 RO/immutable로 읽어 해당 세션만
  conversation_chunk로 ship → neurons 수정 불필요. capture에 私 raw session_id 보관. defer/pointer/
  deferred-state/--enable-hermes-ship 전부 제거. AGENTS 경계는 "shipper가 adapter로 소스 읽어 redacted
  transcript 전달"로 정직 확장(session-memory build/GC/RAGFlow 금지 유지). 전체 122 통과 +
  end-to-end smoke(conversation_chunk, 해당 세션만, secret redact, store 불변/RO, WAL sidecar 없음).

## M6 Defer-gate redesign (SoT regression after system-architecture review) — SUPERSEDED by M7
- status: done
- note: system-architecture review found cross-repo gap — neurons ingress allowlist rejects session_pointer
  and has no pointer consumer (verified directly). User chose "design 재논의" → regressed to grill-to-spec,
  updated requirements/design SoT.
- evidence: ship now defer-gated. drain holds hermes as `deferred` by default (no POST, no quarantine,
  network_used=false); `--enable-hermes-ship` flips to pointer ship. Spool gained `deferred` parked state
  (active depth_counts shape unchanged). Tests: defer + enabled-ship + regression green; full suite 120 passed.
  Drain defer smoke confirmed (status deferred, parked in deferred/, no network). Cross-Repo Contract (neurons
  allowlist + pointer consumer) documented in design.md as enable precondition + tracked follow-up.
