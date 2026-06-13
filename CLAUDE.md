# CLAUDE.md

이 파일은 Claude Code가 `dendrite`에서 작업할 때 따르는 provider overlay다.
공통 운영 계약은 `AGENTS.md`를 우선한다.

## Contract

- 자연어 응답과 문서는 한국어로 작성한다.
- 코드 식별자, CLI 이름, 파일명, endpoint 이름은 영어 원문을 유지한다.
- `dendrite`를 Mac thin-client로만 다룬다:
  provider hook -> locator-only spool/outbox -> thin shipper -> `POST 18080`.
- server/brain/GC 책임은 `neurons`로 보낸다.

## Claude Guardrails

- Hook plan은 non-mutating review artifact다. 실제 provider config 변경은
  명시적 사용자 의도와 rollback path가 있을 때만 수행한다.
- Raw transcript body, private locator, token, raw dataset_id, raw document_id를
  출력하지 않는다.
- `RAGFLOW_API_KEY`, direct RAGFlow write/delete/disable, Docker/SSH/Ubuntu
  runtime mutation을 `dendrite`에 추가하지 않는다.
- `Ledger`, `TranscriptIngestWorker`, session-memory build/promote, brain.query,
  MemoryCard, native memory, GC runner를 import하거나 소유하지 않는다.

## Checks

- `uv run pytest -q`
- `uv run python -m dendrite --help`
- `uv run agy-headless-capture --print "your prompt"`
