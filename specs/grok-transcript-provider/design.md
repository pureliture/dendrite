# Grok Build Transcript Provider — Design Spec

## 승인 상태

- **approved** (사용자 pre-approve, 2026-07-09)

## Overview

`dendrite`에 Grok Build (`grok` CLI) provider를 추가한다. live 훅 이벤트는 research gate
실측 기준 **`Stop`**, locator SoT는 `updates.jsonl`, migrate는 live와 동일한
`normalize_provider_capture_request` → spool → drain 경로를 재사용한다.

## Requirements Reference

- Phase 1 source: `specs/grok-transcript-provider/requirements.md` (**pre-approved**)
- Research evidence: `docs/live/grok-build-research-gate.md`
- 핵심 FR: FR-D1…D8 (provider 등록, contract, capture, resolve, drain, hook-plan, migrate, doctor)

## Architecture

```text
Grok Stop hook (or migrate enumerator)
        │ stdin JSON / synthetic payload
        ▼
normalize_provider_capture_request("grok", …)   # locator-only
        │
        ▼
TranscriptCaptureSpool
        │
        ▼
transcript-drain → JsonlSourceAdapter → conversation_chunk → POST 18080
```

Components:

| Unit | Responsibility |
| --- | --- |
| allowlists | `SUPPORTED_PROVIDERS`, `SUPPORTED_TRANSCRIPT_PROVIDERS`, `MIGRATION_PROVIDERS` include `grok` |
| `ProviderSourceContract` | grok contract: hook_event=`Stop`, locator field=`transcriptPath`, unverified until install smoke |
| `normalize_provider_capture_request` | sessionId + transcriptPath/resolve → capture request |
| `_resolve_grok_session_locator` | GROK_HOME/sessions/**/<sessionId>/updates.jsonl |
| `JsonlSourceAdapter` | default adapter for grok (opaque updates.jsonl) |
| `transcript_migrate` | enumerate only `updates.jsonl` per session dir |
| `build_provider_hook_plan` | non-mutating plan for `~/.grok/hooks` Stop entry |

## Data Flow

1. **Live:** Stop stdin (`hookEventName`/`hook_event_name`, `sessionId`, optional `transcriptPath`, `cwd`/`workspaceRoot`) → normalize → spool.
2. **Migrate:** walk `$(GROK_HOME)/sessions` → each `…/<session-id>/updates.jsonl` → synthetic payload with path + session_id → same normalize → spool.
3. **Drain:** unchanged; `adapter_for("grok")` → JsonlSourceAdapter.

## Component Details

### Locator resolution

Priority:

1. `transcript_path` / `transcriptPath` if present and valid handle (existing generic keys; prefer real file when resolving grok-specific).
2. Else `sessionId`/`session_id` under `GROK_HOME` (default `~/.grok`) / `sessions/**/<id>/updates.jsonl`.
3. Symlink → reject / empty (no fabricate).

### Event mapping

`Stop` / `stop` (and payload `hookEventName`) → capture `event_type=session_end` (codex-aligned).

### Migration identity

- Session id = parent directory name of `updates.jsonl` (optional cross-check `summary.json` `info.id` not required for v1).
- Do not glob all `*.jsonl` (would pick `chat_history.jsonl`).

### Hook plan

- Target: `~/.grok/hooks/*.json` style plan artifact (non-mutating).
- Event: `Stop`.
- argv: `transcript-capture --provider grok --stdin-json --non-fatal`.
- Install blocked while `source_status != source_locator_verified`.

## Error Handling

| Case | Behavior |
| --- | --- |
| missing updates.jsonl | empty locator / migrate skip+count |
| symlink locator | policy block / skip |
| drain unreadable | quarantine class `source_unreadable` |
| unsupported provider regression | allowlist tests |

## Testing Strategy

- Unit: contract registration, normalize Stop payload (camelCase), resolve by session id, migrate enumerates only updates.jsonl, hook-plan blocked_source_unproven, doctor includes grok.
- Boundary: existing boundary tests still pass.
- No live POST required for green; fake ingress optional.

## TDD Strategy

red → green → refactor per milestone. Tests first for capture/migrate/contract.

## Milestones

- **M1** — Allowlists + contract + hook-plan/doctor surface — evidence: unit tests green
- **M2** — Capture normalize + locator resolve (Stop, transcriptPath, sessionId) — evidence: unit tests
- **M3** — Migrate enumerate/spool grok-only updates.jsonl — evidence: unit tests + dry-run shape
- **M4** — Regression full `uv run pytest -q` + mid-flight architecture/simplifier review

## Open Questions

- Interactive TUI SessionEnd (out of scope for default plan).
- Live install remains operator-owned after source verification.
