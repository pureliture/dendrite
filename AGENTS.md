# AGENTS.md

이 저장소는 OpenClaw/LLM-brain의 Mac thin-client인 `dendrite`를 소유한다.
역사적 `workspace-ragflow-advisor`의 provider/capture 지침 중 client 책임만
가져온다. Ubuntu RAGFlow 운영 agent 지침은 이 repo의 truth가 아니다.

## Identity

- 자연어 응답과 문서는 한국어로 작성한다.
- 코드 식별자, 파일명, CLI 이름, endpoint 이름은 영어 원문을 유지한다.
- `dendrite`는 brain/server가 아니다. Mac side provider hook, locator-only
  capture spool/outbox, bounded thin shipper, approved ingress endpoint로의
  `POST 18080`까지만 책임진다.
- Server/brain/GC 책임은 `neurons` repo가 소유한다.

## Source Of Truth

- 공통 repo 계약: `AGENTS.md`
- Claude provider overlay: `CLAUDE.md`
- Gemini/Antigravity provider overlay: `GEMINI.md`
- 현재 command surface와 boundary: `README.md`
- boundary regression guard: `tests/test_client_boundary.py`,
  `tests/test_repo_instructions.py`

## Runtime Boundary

Boundary string: provider hook -> locator-only spool/outbox -> thin shipper ->
`POST 18080`.

Allowed:

- provider hook payload normalization
- locator-only local capture/spool/outbox
- thin shipper enqueue to `POST 18080`
- public/redacted enqueue payload contract
- local-only diagnostics and dry-run hook plans
- `agy-headless-capture` launch-dir label plus transcript locator capture

Forbidden by default:

- `Ledger` ownership
- `TranscriptIngestWorker` ownership
- direct RAGFlow write/delete/disable
- RAGFlow API credential handling
- session-memory build/promote/read SoT
- brain.query, MemoryCard, native memory
- GC live execute or GC scheduler wiring
- Ubuntu runtime mutation, Docker/RAGFlow management, or `ssh ragflow-ubuntu`

## Safety Lines

- Do not read raw private transcript/source contents unless the user explicitly
  asks for that exact access.
- Do not print private locators, raw private paths, tokens, cookies, bearer
  strings, API keys, raw transcript body, raw dataset_id, or raw document_id.
- Provider hooks must stay locator-only. They may write private spool requests
  and kick a shipper, but must not call RAGFlow, NATS, Docker, SSH, or GC.
- `RAGFLOW_API_KEY` belongs to server/runtime lanes, not to `dendrite`.

## Workflow

- Python execution and tests use `uv`.
- Use `rg` for search and `apply_patch` for manual edits.
- Keep public CLI/API compatibility unless the user explicitly approves a break.
- Do not add skip/xfail just to make tests pass.
- Before claiming completion, run relevant tests such as `uv run pytest -q`.
- If `graphify-out/graph.json` exists and the user asks codebase questions,
  prefer `graphify query`, `graphify path`, or `graphify explain` before broad
  source browsing.
