# Grok Build research gate (dendrite)

Sanitized evidence only. No raw private paths, session ids, or transcript bodies.

## Environment

| Item | Value |
| --- | --- |
| Date (local) | 2026-07-09 |
| `grok --version` | `grok 0.2.93 (f00f96316d4b) [stable]` |
| Docs root | `$GROK_HOME/docs/user-guide/` (default `~/.grok`) |
| Sessions with `updates.jsonl` (after smokes) | 8 |

## Document citations

### Sessions (`17-sessions.md`)

- Base: `~/.grok/sessions/` unless `GROK_HOME` overrides base dir.
- Layout: `~/.grok/sessions/<encoded-cwd>/<session-id>/`
- Files (doc): `summary.json` (metadata), `updates.jsonl` (ACP stream; **authoritative** conversation log for `/resume` and restore).
- Live sibling files observed (names only): `announcement_state.json`, `chat_history.jsonl`, `events.jsonl`, `prompt_context.json`, `prompts/`, `resources_state.json`, `rewind_points.jsonl`, `signals.json`, `summary.json`, `summary.json.lock`, `system_prompt.txt`, `terminal/`, `updates.jsonl`.

### Hooks (`10-hooks.md`)

- Global hooks: `~/.grok/hooks/*.json`
- Events include `Stop` (“An agent turn ends”) and `SessionEnd` (“The session ends.”) — both first-class, non-blocking.
- Cursor alias: `sessionEnd` → `SessionEnd`.
- Lifecycle events (matcher rejected): `SessionStart`, `SessionEnd`, `Stop`, `UserPromptSubmit`.
- Env always injected: `GROK_HOOK_EVENT`, `GROK_HOOK_NAME`, `GROK_SESSION_ID`, `GROK_WORKSPACE_ROOT` (+ Claude aliases).
- Doc stdin example is PreToolUse-shaped; includes `sessionId`, `cwd`, `workspaceRoot`.

### Config (`05-configuration.md`)

- `GROK_HOME` overrides config directory (default `~/.grok`).

## summary.json (live sample, keys only)

Top-level keys observed:

`agent_name`, `chat_format_version`, `created_at`, `current_model_id`, `generated_title`, `git_remotes`, `git_root_dir`, `grok_home`, `head_branch`, `head_commit`, `info`, `last_active_at`, `next_trace_turn`, `num_chat_messages`, `num_messages`, `reasoning_effort`, `request_id`, `sandbox_profile`, `session_summary`, `updated_at`

Nested: `info.cwd`, `info.id` (36-char session id).  
**No top-level `session_id` / `id` field.** Migrate session identity should prefer **directory name** and/or `info.id`, not a missing top-level id.

Group directory names are URL-encoded cwd (`%2F…` style).

## updates.jsonl structure (live sample, one session)

| Metric | Value |
| --- | --- |
| Lines | 179 |
| Top-level `method` counts | `session/update` (154), `_x.ai/session/update` (25) |
| `params.update.sessionUpdate` values | `tool_call_update` (87), `tool_call` (34), `hook_execution` (20), `agent_thought_chunk` (17), `agent_message_chunk` (10), `user_message_chunk` (6), `turn_completed` (5) |

First-line key shape (types only):

```text
timestamp: int
method: str
params.sessionId: str
params.update.sessionUpdate: str
params.update… (varies)
params._meta.eventId / agentTimestampMs
```

Opaque ship of full file as `conversation_chunk` remains viable for dendrite v1; native parsing is neurons scope.

## Live hook smoke (headless `grok -p`)

Method:

1. Temporary global hook under `~/.grok/hooks/` (removed after research).
2. Logger writes **hashed/redacted** ndjson only (`docs/live/sessionend-smoke.ndjson`).
3. Commands: `grok -p "…" --output-format json` from `/tmp`.

### Results (repeated across runs)

| Event | Fired on headless `-p`? | stdin keys (observed) | Notes |
| --- | --- | --- | --- |
| `session_start` / SessionStart | **Yes** | `cwd`, `hookEventName`, `sessionId`, `source`, `timestamp`, `workspaceRoot` | No `transcriptPath` |
| `stop` / Stop | **Yes** (end of turn) | `cwd`, `hookEventName`, `promptId`, `reason`, `sessionId`, `timestamp`, `transcriptPath`, `workspaceRoot` | `reason` example: `end_turn` |
| `session_end` / SessionEnd | **No** (not observed) | — | Doc lists event; **headless exit did not fire it** in these smokes |

### Stop `transcriptPath` (basename only)

From `docs/live/stop-transcript-basename.ndjson` (sanitized — no raw session id):

- `transcript_basename`: **`updates.jsonl`**
- `transcript_is_updates_jsonl`: **true**
- `transcript_exists`: **true**
- parent directory name length: **36** (session id shaped; value redacted to len + hash only)

So for live capture, Stop payload already carries a usable locator to SoT when present; sessionId + GROK_HOME resolve remains a fallback.

### Headless JSON stdout keys

`requestId`, `sessionId`, `stopReason`, `text`, `thought` (sessionId present, len 36).

### Interactive TUI SessionEnd

**Not smoke-tested** in this gate (requires interactive quit). Document support exists; live proof open.

## Implications for requirements

1. **SessionEnd-only live default is incomplete for headless `grok -p`.** Research shows Start+Stop only.
2. **Decision (2026-07-09):** live default hook event = **`Stop`** (not SessionEnd).
3. Stop fires per turn; with shared `locator_version_hash` (mtime/size), multi-turn sessions re-ship when the file grows (same as codex/claude identity rules).
4. Prefer locator from `transcriptPath` when present (Stop); else resolve `sessionId` → `updates.jsonl`.
5. Migrate enumeration: only `updates.jsonl` per session dir; session id from **directory name** / `info.id`.
6. Hook install still operator-owned after approved plan; temporary research hook removed.

## Open after this gate

- [ ] Interactive TUI: does quitting fire `SessionEnd`?
- [ ] Multi-turn interactive: Stop count vs SessionEnd once.
- [ ] Whether `GROK_HOME` is set in hook env when using default `~/.grok` (smoke: env hash empty → unset is normal).

## Artifacts (this worktree)

| Path | Content |
| --- | --- |
| `docs/live/sessionend-smoke.ndjson` | Redacted hook records (start/stop) |
| `docs/live/stop-transcript-basename.ndjson` | Basename-only transcriptPath check (session id redacted) |

Temporary research hooks under `~/.grok/hooks/` and local one-off logger scripts were **removed** after smoke.
