# Hermes Provider Capture Requirements

## 승인 대상

- Source of truth: `requirements.md`
- Preview companion: `requirements.html` (필요 시 생성)
- 승인 상태: pre-approved by user directive (grill 자문자답 기반)

## 질문-답변 흐름

각 질문은 grill-to-spec gray-box 전략으로 상위 설계/제품/경계 판단을 다룬다.
사용자 지시에 따라 자문자답으로 채우되, 답은 코드베이스 리서치(7개 sonnet
서브에이전트) 및 Hermes 공식 문서 조사 근거에 기반한다.

### Q: 무엇을 하려는가?

`dendrite`가 Hermes agent 세션을 식별하고, 기존 Codex/Claude/Gemini/Antigravity
capture와 같은 source identity 체계로 `locator -> outbox(spool) -> thin shipper ->
approved ingress POST` 경로에 태울 수 있게 한다. `dendrite`는 Mac thin-client이며
server brain, MemoryCard, session-memory build, GC, RAGFlow write는 구현하지 않는다.

### Q: Hermes는 세션을 어디에 어떤 형식으로 저장하는가? (locator의 전제)

Hermes Agent(Nous Research)는 세션을 단일 SQLite DB에 저장한다.

- 기본 위치: `~/.hermes/state.db` (override env: `HERMES_HOME` -> `$HERMES_HOME/state.db`)
- 형식: SQLite (WAL: `state.db-wal`, `state.db-shm`). `sessions`/`messages` 테이블.
- 과거 per-session JSONL 방식은 SQLite로 대체됨(현행 아님).

이는 codex/claude/gemini/antigravity가 쓰는 per-session `*.jsonl` 파일 모델과
근본적으로 다르다. 따라서 기존 jsonl glob migration과 transcript body를 텍스트로
읽어 redact하는 drain 경로는 Hermes에 그대로 적용되지 않는다.

### Q: 이 차이를 어떻게 다룰 것인가? (최종 결정 — 재논의 후)

**source adapter 인터페이스 1개 + provider별 adapter.** capture는 다른 provider처럼
locator-only(store 경로만 기록, 본문 미열람)로 두고, drain(thin shipper) 시점에
`adapter_for(provider)`로 소스를 읽어 모두 동일한 `conversation_chunk`로 ship한다.

- jsonl provider(codex/claude/gemini/antigravity): `JsonlSourceAdapter`가 파일 텍스트를
  읽어 redact(기존 동작 보존).
- Hermes: `HermesSqliteSourceAdapter`가 `state.db`를 **read-only/immutable**로 열어 해당
  세션 메시지만 조회 → redact → conversation_chunk.
- Hermes도 결국 `conversation_chunk`를 ship → neurons가 이미 수용 → **neurons 수정 불필요,
  즉시 end-to-end**.
- dendrite의 thin 경계는 "provider 로컬 소스를 읽어 redacted transcript 문서 생산"까지로
  유지된다(jsonl로 이미 하던 일). session-memory build/promote, GC, RAGFlow write는 계속
  neurons 소유. (구조적 이유: DB가 Mac 로컬이라 server가 직접 못 읽음 → Mac 쪽 추출 필요.)

### Q: capture를 무엇이 trigger하는가? hook이 있는가?

Hermes는 hook이 있다(공식 docs 확인): shell hook을 `~/.hermes/config.yaml`에 등록해
세션종료 이벤트(`on_session_end`/`session:end`)에 임의 명령 실행 가능, stdin JSON에
`session_id`/`cwd` 제공(단 transcript/DB 경로는 미제공 → dendrite가 store 해석). 따라서
capture는 hook 또는 explicit invocation으로 trigger되고, hook 자동설치는 다른 provider와
동일하게 deferred(operator 수동 연결, `hook-plan`은 non-mutating).

### Q: locator를 어떻게 결정/검출하는가? (location 명확 vs 불명확)

위치는 명확(`~/.hermes/state.db`)하므로 locator resolver를 구현하되, 안전하게:

- 우선순위: payload의 명시 locator 키 -> `HERMES_HOME` env -> 기본 `~/.hermes/state.db`.
- detector는 파일 **존재 여부만** 확인한다. SQLite를 open/query하지 않는다.
- 파일이 없으면 source를 날조하지 않고 skip/no-source 상태로 처리한다.

### Q: live 검증은 어디까지 하는가?

이 머신에 Hermes가 설치되어 있다는 보장이 없고, 외부 store/format은 1차 live
검증이 불가하다. 따라서 contract는 `native_parser_status`/`verification_status`를
unverified로 명시하고, hook install은 deferred로 둔다. 본 scope의 검증은 unit test +
local/fake-server synthetic smoke까지이며, live `POST`나 실제 Hermes 세션 생성은 하지
않는다.

### Q: 기존 4개 provider 동작은 어떻게 보호하는가?

Hermes 분기는 좁고 명시적으로(`provider == 'hermes'`) 추가하고, 기존 jsonl provider의
body-shipping 경로는 건드리지 않는다. provider 식별/contract/drain regression test로
codex/claude/gemini/antigravity가 그대로 식별되고 body를 ship함을 증명한다.

### Q: (이력) pointer 접근은 왜 폐기했나?

초기엔 dendrite가 SQLite를 안 열고 "locator pointer" 문서만 ship하는 설계였다. 그러나
system-architecture 리뷰가 neurons ingress validator(`IngestJobValidator.DOCUMENT_KINDS`,
closed allowlist)에 pointer kind가 없어 fail-closed(400) 거부됨을 발견(직접 확인). 사용자
재논의에서 "DB니까 그냥 조회하면 된다 + 인터페이스 1개+adapter로 깔끔히" 방향으로 전환.
adapter가 conversation_chunk를 만들면 neurons가 이미 수용하므로 pointer/neurons 수정이
불필요해져 폐기했다.

### Q: spec/artifact는 어디에 남기는가?

이 branch/worktree의 작업 SoT로만 유지한다. 별도 승인 없이 공식 문서/doc registry로
승격하지 않는다.

## 기능 요구사항

- Hermes provider가 source identity 전 구간에서 canonical 문자열 `hermes`로 식별된다.
- `hermes`를 기존 provider allowlist 전부와 일관되게 등록한다(식별 site 누락 금지).
- Hermes locator resolver를 추가한다: 명시 config locator -> `HERMES_HOME` -> 기본
  `~/.hermes/state.db` 순으로 해석한다.
- Hermes capture는 store 파일 존재만 확인하며(capture 시점 SQLite open 금지) 기존
  `locator_only` content policy의 capture request를 생성, `validate_capture_request`를 통과한다.
- capture request의 public 표면(public_summary/CLI 출력)에는 provider, project, locator
  해시/version hash, timestamp만 포함한다. 원경로와 raw session_id는 private spool에만 둔다.
- drain은 source adapter 인터페이스 1개로 소스를 읽어 모든 provider를 동일한
  `conversation_chunk`로 ship한다. shipper는 기존 approved ingress(`POST /v1/ingest/enqueue`)만
  사용하고 credential을 URL/headers에 넣지 않는다.
- Hermes adapter는 `state.db`를 read-only/immutable로 열고(write/WAL checkpoint 금지) 해당
  세션 메시지만 추출해 redact한다. 읽기 불가/스키마 불일치는 quarantine(크래시 아님).
- redaction은 기존 `redact_public_ingress_text`를 재사용한다. shipped body/metadata에 원경로,
  raw session_id, secret이 포함되지 않는다.
- Hermes hook 자동 설치는 deferred. `hook-plan`은 non-mutating(`blocked_source_unproven`) plan을 낸다.
- raw transcript body, private locator(원경로), token/cookie/bearer, dataset_id/
  document_id는 stdout/report/public metadata로 출력하지 않는다.
- 기존 codex/claude/gemini/antigravity의 식별·capture·drain·CLI 호환성을 깨지 않는다(adapter
  도입은 jsonl 동작 보존).
- Hermes를 jsonl glob 기반 `transcript-migrate` 대상에 포함하지 않는다(store가 단일
  SQLite라 glob backfill이 의미 없음). 이유를 design/docs에 명시한다.
- Hermes enable 방법, 안전 경계, 샘플 설정을 문서화한다.

## 비기능 요구사항

| 항목 | 요구값 |
| --- | --- |
| Worktree isolation | 모든 파일 변경은 `claude/hermes-provider-capture` worktree에서 수행한다. `main`에는 직접 쓰지 않는다. |
| Source of truth | Phase 1은 `requirements.md`, Phase 2는 `design.md`가 SoT다. HTML은 preview일 뿐이다. |
| TDD | code-changing work는 red -> green -> refactor. 동작 test를 먼저 추가하고 fail 확인 후 production code 작성. |
| Thin-client boundary | dendrite는 provider 소스를 읽어 redacted transcript 문서(conversation_chunk)를 만드는 것까지만 한다(이미 jsonl로 하던 일). session-memory build/promote, GC, RAGFlow write, MemoryCard는 neurons 소유. |
| Source read | capture는 locator-only(본문 미열람). drain은 source adapter로 소스를 읽되 Hermes SQLite는 read-only/immutable(write/checkpoint 금지). |
| Redaction | public 표면은 hash/공개 metadata만. 원경로/secret/raw body 미노출. 기존 redaction 규칙 재사용. |
| Approved endpoint | shipper는 `POST /v1/ingest/enqueue`만 사용. URL에 credential 금지. |
| Verification posture | Hermes contract는 parser/verification unverified, hook install deferred로 명시. live POST/실세션 생성은 별도 승인 gate. |
| Compatibility | 기존 CLI/hook payload/enqueue wire format 미변경. 기존 4개 provider regression 금지. |
| Boundary tests | `test_client_boundary.py` 금지 import/문자열을 새 코드가 위반하지 않는다. |

## 사용자 시나리오

- Operator가 Hermes를 쓰는 환경에서 capture(hook 또는 explicit) → drain으로 Hermes 세션을
  다른 provider와 동일한 `conversation_chunk`로 neurons에 보낼 수 있다(neurons 수정 불필요).
- Maintainer가 provider를 추가/점검할 때 Hermes가 다른 provider와 동일한 식별 체계 +
  source adapter 인터페이스로 통합되어 있음을 확인한다.
- Reviewer가 `requirements.md`/`design.md`만 보고 adapter 구조(왜 Hermes는 SQLite adapter)와
  thin 경계(transcript 문서 생산까지)를 재구성할 수 있다.
- Security reviewer가 Hermes 경로에서 raw body/원경로/secret/raw session_id가 출력되지 않고,
  SQLite가 read-only로만 열리며 store가 수정되지 않음을 test로 확인한다.

## 검증 완료 기준

- L0 경계 게이트: 기존 boundary/repo-instruction test 통과. 새 코드가 thin-client 금지
  소유권(session-memory build/promote, GC, RAGFlow write/credential)을 침범하지 않는다.
- L1 자동 검증: `uv run pytest -q` 통과. 최소 포함: provider contracts(hermes 포함),
  hermes capture/drain(SQLite adapter), client boundary, jsonl regression.
- L2 로컬 런타임 스모크: capture가 locator-only JSON report를 만들고 원경로/raw/credential을
  출력하지 않는다. drain(합성 SQLite store)이 해당 세션만 `conversation_chunk`로 ship,
  타 세션 제외, secret redact, store mtime/size 불변(read-only), WAL sidecar 미생성.
- L3 (별도 승인): 실제 neurons endpoint로의 live `POST` canary, 실제 Hermes 세션 생성/열람.
- 증거에는 Hermes가 conversation_chunk로 ship되고 본문/메타/source에 원경로·raw session_id가
  없으며, 기존 4개 provider가 그대로 동작함이 포함된다.

## 허용 / 금지 범위

- 허용: Hermes capture(locator-only) + drain의 SQLite read-only 추출 → redacted
  conversation_chunk ship, bounded local/recording-ingress smoke, JSON report 검증.
- 별도 승인 필요: 실제 neurons endpoint로의 live `POST` canary, 실제 Hermes 세션 생성/열람.
- 금지: Hermes DB write/delete, WAL checkpoint trigger, raw transcript body 출력, private
  locator(원경로)/raw session_id 출력, token/cookie/bearer 출력, `RAGFLOW_API_KEY` 취급,
  dataset_id/document_id 출력, `Ledger`/`TranscriptIngestWorker`/brain/server/GC/RAGFlow
  management 실행, session-memory build/promote.

## 미결정 항목 / 후속작업

- Hermes SQLite 스키마(테이블/컬럼명)의 live 설치 대상 검증. 현재 문서 기준 가정 + 스키마
  tolerant 구현 + 합성 픽스처 테스트. live-smoke 후 contract를 verified로 승격 가능.
- Hermes shell hook 자동설치 플랜(`~/.hermes/config.yaml`) 생성은 추후(현재 deferred).
- 위 항목들은 dendrite 동작을 막지 않는다. 구현 중 추가 SoT 변경이 필요하면 `grill-to-spec`
  상류로 회귀한다.
