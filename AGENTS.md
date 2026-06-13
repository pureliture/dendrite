# AGENTS.md

이 저장소는 OpenClaw/LLM-brain의 Mac thin-client인 `dendrite`를 소유한다.

## Identity

- 자연어 응답과 문서는 한국어로 작성한다.
- 코드 식별자, 파일명, CLI 이름, endpoint 이름은 영어 원문을 유지한다.
- `dendrite`는 brain/server가 아니다. Mac side capture, locator-only spool,
  thin shipper, approved ingress endpoint로의 POST까지만 책임진다.

## Boundary

Allowed:

- provider hook payload normalization
- locator-only local capture/spool/outbox
- thin shipper enqueue to `POST 18080`
- public/redacted enqueue payload contract
- local-only diagnostics and dry-run checks

Forbidden by default:

- `Ledger` ownership
- `TranscriptIngestWorker` ownership
- direct RAGFlow write/delete/disable
- session-memory build/promote/read SoT
- brain.query, MemoryCard, native memory
- GC live execute or GC scheduler wiring
- Ubuntu runtime mutation

## Workflow

- Python execution and tests use `uv`.
- Keep public CLI/API compatibility unless the user explicitly approves a break.
- Do not add skip/xfail just to make tests pass.
- Prefer small commits that map to the active milestone.
