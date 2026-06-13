# GEMINI.md

이 파일은 Gemini/Antigravity 계열 agent가 `dendrite`를 읽을 때 따르는
repo-local provider overlay다. 공통 운영 계약은 `AGENTS.md`를 우선한다.

## Contract

- 자연어 응답과 문서는 한국어로 작성한다.
- 코드 식별자, CLI 이름, 파일명, endpoint 이름은 영어 원문을 유지한다.
- Antigravity/headless capture는 launch-dir label과 transcript locator만
  private spool에 남긴다.
- `dendrite`의 안정 경로는 provider hook -> locator-only spool/outbox ->
  thin shipper -> `POST 18080`이다.

## Safety

- Provider payload나 Antigravity transcript body를 public output에 쓰지 않는다.
- RAGFlow direct write/delete/disable, RAGFlow credential, NATS, Docker, SSH,
  Ubuntu runtime mutation을 이 repo에 추가하지 않는다.
- Historical component 이름이 `transcript-ingest`처럼 보여도 동작으로
  분류한다. locator-only POST면 client seam이고, ledger/state/build/reconcile이면
  `neurons` 책임이다.

## Checks

- `uv run pytest -q`
- `uv run python -m dendrite --show-boundary`
