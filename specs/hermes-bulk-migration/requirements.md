# Hermes Bulk Migration Requirements

## 승인 대상

- Source of truth: `requirements.md`
- Preview companion: `requirements.html` (필요 시)
- 승인 상태: pre-approved by user directive (grill 자문자답)

## 질문-답변 흐름

자문자답. 답은 코드베이스(`transcript_migrate.py`, adapter) + 맥미니 실측 스키마(18세션,
`messages.session_id`) 근거. 외부 리서치 불요(스키마는 실 DB probe로 확인됨).

### Q: 무엇을 하려는가?

기존에 쌓인 Hermes 과거 세션(현재 맥미니 `~/.hermes/state.db`에 18개)을 일괄(bulk)로
backfill한다. 즉 세션별로 locator-only capture request를 spool에 채워, 기존
`transcript-drain`이 각 세션을 adapter로 읽어 `conversation_chunk`로 ship하게 한다.

### Q: 왜 기존 `transcript-migrate`로 안 되나?

기존 migration은 provider별 store를 `**/*.jsonl` glob으로 파일 단위 열거한다. Hermes는
세션이 파일이 아니라 단일 SQLite DB의 행(`messages`, 세션키 `session_id`)이라 glob이
적용되지 않는다. 그래서 **SQLite-aware enumerator**가 필요하다.

### Q: 어떻게 할 것인가? (핵심)

기존 migration 루프에 Hermes 분기를 추가한다.

- Hermes store(`state.db`)를 **read-only/immutable**로 열어 `messages`의 distinct
  `session_id`(=메시지를 가진 세션)를 열거한다(내용 미열람).
- 세션마다 capture request 1개를 만든다: locator=`state.db` 경로, 私 `session_id`=그 세션.
  request는 기존 단건 capture와 동일 schema(locator-only).
- spool에 쌓으면, 기존 `transcript-drain`이 세션별로 `HermesSqliteSourceAdapter`로 읽어
  `conversation_chunk`로 ship한다(단건 검증과 동일 경로).

### Q: 어떤 세션을 대상으로 하나?

기본은 **메시지를 가진 모든 세션**(messages distinct session_id). 날짜/상태 필터는
이번 scope 제외(YAGNI). `--limit N`으로 스모크용 일부만, `--dry-run`으로 안 보내고 카운트만.

### Q: 멱등성은?

re-run 안전해야 한다. spool의 `write_json_once`는 request_id로 멱등하고, request_id는
(provider, event_type, session_id, locator_hash, version_hash) identity에서 파생 →
같은 세션·같은 store 상태면 같은 request_id. ingress 멱등키는 content_hash 기반.
따라서 재실행해도 동일 세션이 중복 적재되지 않는다.

### Q: 안전 경계는?

DB는 read-only/immutable로만 연다(write/checkpoint 금지). 세션 내용·raw session_id·
원경로를 stdout/report로 출력하지 않는다(카운트만). dry-run은 spool도 안 쓴다(완전 안전).
ship 자체는 기존 drain 책임이며 live endpoint 검증은 이번 scope 밖(나중).

### Q: 실행/검증은 어디까지?

`transcript-migrate --provider hermes --dry-run`을 맥미니 실 DB로 돌려 18세션 카운트까지
증명(read-only, 안전). 실제 spool/ship은 단건 검증과 동일 경로이므로 unit/integration
테스트 + dry-run으로 충분. live neurans POST는 endpoint 확정 후 별도.

## 기능 요구사항

- `transcript-migrate --provider hermes`가 동작한다(`MIGRATION_PROVIDERS`에 hermes 포함,
  CLI choices 자동 반영).
- Hermes enumerator는 `state.db`를 read-only/immutable로 열어 `messages`의 distinct
  `session_id`를 열거한다(내용 미열람). 세션키 컬럼이 없으면 단일 세션으로 처리.
- 세션마다 locator-only capture request(locator=state.db, 私 session_id)를 만든다.
- Hermes store 경로 해석: `--source-root hermes=/path/to/state.db` 우선 →
  `HERMES_HOME` → 기본 `~/.hermes/state.db`. store는 dir이 아니라 file로 취급한다.
- `--dry-run`은 spool 없이 세션 수만 카운트한다. `--limit N`은 처음 N개만.
- migration report는 hermes의 found/spooled/errors를 카운트로만 보고한다(원경로/세션id/
  내용 미포함).
- re-run 멱등: 동일 세션·store 상태에서 중복 spool하지 않는다.
- 기존 jsonl provider migration(codex/claude/gemini/antigravity)을 깨지 않는다.
- 세션 ship은 기존 `transcript-drain` + `HermesSqliteSourceAdapter`를 재사용한다(새 ship
  경로를 만들지 않는다).

## 비기능 요구사항

| 항목 | 요구값 |
| --- | --- |
| Worktree isolation | `claude/hermes-provider-capture` worktree에서 작업. main 직접 수정 금지. |
| TDD | red -> green -> refactor. 동작 test 먼저. |
| Source read | Hermes enumerate는 read-only/immutable(write/checkpoint 금지). 내용 미열람(세션키만). |
| Idempotency | re-run 시 동일 세션 중복 적재 금지. |
| Redaction/secrecy | report·stdout에 원경로/raw session_id/세션 내용 미출력(카운트만). |
| Thin-client boundary | session-memory build/promote, GC, RAGFlow write 미추가(neurons 소유). |
| Compatibility | 기존 jsonl migration·CLI·wire format 무회귀. |
| Boundary tests | `test_client_boundary`/`test_repo_instructions` 통과. |

## 사용자 시나리오

- Operator가 `transcript-migrate --provider hermes --dry-run`으로 과거 Hermes 세션 수를
  안전하게 확인한다(맥미니: 18).
- Operator가 `--limit`로 일부 세션을 spool해 스모크한 뒤, 전체를 spool하고 `transcript-drain`으로
  세션별 conversation_chunk를 ship한다.
- Maintainer가 재실행해도 같은 세션이 중복 적재되지 않음을 확인한다.

## 검증 완료 기준

- L1 자동 검증: `uv run pytest -q` 통과. hermes migration(enumerate/per-session request/
  dry-run/limit/idempotent) + jsonl migration 무회귀 + boundary 가드.
- L2 로컬 스모크: 맥미니 실 `state.db`로 `--dry-run`이 18세션을 카운트(read-only, 원경로/
  세션id/내용 미출력). `--limit 1`로 단건 spool→drain이 conversation_chunk 1건 생성(가짜
  ingress) 확인.
- L3 (별도): 실제 neurons endpoint로의 전체 ship(endpoint 확정 후).
- 증거에 enumerate가 read-only(store mtime/size 불변), 멱등 re-run(중복 spool 0), report가
  카운트만 포함함이 들어간다.

## 허용 / 금지 범위

- 허용: Hermes 세션 열거(read-only) + 세션별 locator-only spool, `--dry-run`/`--limit`,
  단건/가짜-ingress 검증, 실 DB dry-run 카운트.
- 별도 승인: 실제 neurons endpoint로의 live ship.
- 금지: Hermes DB write/delete, WAL checkpoint, 세션 내용/raw session_id/원경로 출력,
  session-memory build/promote, GC/RAGFlow management.

## 미결정 항목

- Hermes store 스키마는 맥미니 실측 확인됨(`messages.session_id/role/content/timestamp`).
  타 설치 변형은 enumerator를 스키마-tolerant하게 유지하고, 불일치 세션은 0건/오류 카운트로 처리.
- live ship endpoint는 이번 scope 밖(나중 검증).
