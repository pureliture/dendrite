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

### Q: 이 차이를 thin-client 경계 안에서 어떻게 다룰 것인가? (핵심 결정)

dendrite는 Hermes의 SQLite body를 읽거나 파싱하지 않는다. SQLite를 transcript로
파싱/추출하는 것은 session-memory/transcript build에 해당하며 이는 neurons(server/
brain)의 책임이다. 따라서 Hermes는 **locator-only pointer provider**로 통합한다.

- dendrite는 Hermes 세션 store의 locator(설정된 DB 경로)와 안전 metadata만 기록한다.
- locator는 path handle로만 다루고 body는 절대 읽지 않는다(파일 존재/`mtime:size`
  기반 version hash까지만).
- 생성되는 envelope는 기존 `agent_knowledge_capture_request.v1` schema와 호환되는
  locator-only capture request이며, 그대로 approved ingress로 ship 가능하다.
- DB로부터 실제 세션 본문을 추출하는 일은 neurons로 명시 위임(이번 scope 밖).

### Q: capture를 무엇이 trigger하는가? live hook인가, explicit invocation인가?

Hermes의 hook/extension API는 현재 1차 자료로 확정되지 않았다. dendrite의 기존
provider도 hook을 자동 설치하지 않고 `hook_install_status='deferred_not_installed'`로
두며 operator가 수동 연결한다. 따라서 Hermes도:

- hook 자동 설치는 deferred. `hook-plan`은 deferred 상태 plan을 non-mutating으로 출력.
- capture entry는 explicit invocation: `dendrite transcript-capture --provider hermes`에
  설정된 DB locator를 공급하는 방식으로 동작.

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

### Q: pointer를 neurons가 실제로 받는가? (구현/리뷰 중 발견 → 재논의)

아니다. neurons ingress validator(`IngestJobValidator.DOCUMENT_KINDS`)는 closed
allowlist이고 `session_pointer` kind가 없다(conversation_chunk 등 8종만 허용). 즉
dendrite가 pointer를 ship하면 fail-closed(400)로 거부되고, neurons엔 pointer를
소비할 consumer도 없다. neurons는 server/brain repo라 이번 dendrite scope 밖이므로
dendrite 단독으로 닫을 수 없다.

### Q: 그래서 ship을 어떻게 하는가? (defer-gate 결정)

Hermes는 capture + spool까지만 동작시키고, **live ship은 기본 비활성(defer-gate)**
한다. drain은 hermes 요청을 POST하지 않고 `deferred` 상태로 보류한다(fail-closed
quarantine 아님). neurons가 pointer 계약(allowlist에 kind 추가 + pointer-aware
consumer)을 갖추면 단일 스위치(`--enable-hermes-ship`)로 ship을 켤 수 있다. 필요한
neurons 계약은 design/docs에 문서화하되 구현은 cross-repo 후속작업으로 분리한다.

### Q: spec/artifact는 어디에 남기는가?

이 branch/worktree의 작업 SoT로만 유지한다. 별도 승인 없이 공식 문서/doc registry로
승격하지 않는다.

## 기능 요구사항

- Hermes provider가 source identity 전 구간에서 canonical 문자열 `hermes`로 식별된다.
- `hermes`를 기존 provider allowlist 전부와 일관되게 등록한다(식별 site 누락 금지).
- Hermes locator resolver를 추가한다: 명시 config locator -> `HERMES_HOME` -> 기본
  `~/.hermes/state.db` 순으로 해석한다.
- Hermes detector는 store 파일 존재만 확인하며 SQLite를 open/read/query하지 않는다.
- Hermes capture는 기존 `locator_only` content policy를 따르는 capture request를
  생성하고, 기존 `validate_capture_request` schema를 통과한다.
- envelope에는 provider, source identity, locator(해시/version hash), timestamp,
  workspace metadata만 안전하게 포함한다. raw/private body는 포함하지 않는다.
- shipper는 기존 approved ingress endpoint(`POST /v1/ingest/enqueue`)만 사용한다.
  새 endpoint를 만들지 않으며 credential을 URL/headers에 넣지 않는다.
- Hermes ship은 기본 비활성(defer-gate)이다. drain은 hermes 요청을 POST하지 않고
  `deferred` 상태로 보류하며, fail-closed quarantine으로 보내지 않는다. SQLite body는
  어느 경우에도 읽지 않는다.
- ship 활성화 시(`--enable-hermes-ship`)에만 locator-only pointer 문서를 approved
  ingress로 보낸다. 이때도 SQLite body는 읽지 않는다(실제 본문 추출은 neurons 위임).
- neurons가 ship을 받기 위한 선행 계약(allowlist에 pointer kind 추가 + pointer-aware
  consumer)을 design/docs에 명시한다. 그 구현은 cross-repo 후속작업으로 분리한다.
- Hermes hook 자동 설치는 deferred. `hook-plan`은 non-mutating deferred plan을 낸다.
- raw transcript body, private locator(원경로), token/cookie/bearer, dataset_id/
  document_id는 stdout/report/public metadata로 출력하지 않는다.
- 기존 codex/claude/gemini/antigravity의 식별·capture·drain·CLI 호환성을 깨지 않는다.
- Hermes를 jsonl glob 기반 `transcript-migrate` 대상에 포함하지 않는다(store가 단일
  SQLite라 glob backfill이 의미 없음). 이유를 design/docs에 명시한다.
- Hermes enable 방법, 안전 경계, 샘플 설정을 문서화한다.

## 비기능 요구사항

| 항목 | 요구값 |
| --- | --- |
| Worktree isolation | 모든 파일 변경은 `claude/hermes-provider-capture` worktree에서 수행한다. `main`에는 직접 쓰지 않는다. |
| Source of truth | Phase 1은 `requirements.md`, Phase 2는 `design.md`가 SoT다. HTML은 preview일 뿐이다. |
| TDD | code-changing work는 red -> green -> refactor. 동작 test를 먼저 추가하고 fail 확인 후 production code 작성. |
| Thin-client boundary | dendrite는 server brain/MemoryCard/session-memory build/GC/RAGFlow write를 추가하지 않는다. SQLite body 파싱은 neurons 위임. |
| Locator-only | Hermes locator는 path handle로만 다룬다. body read 금지, SQLite open/query 금지. |
| Redaction | public 표면은 hash/공개 metadata만. 원경로/secret/raw body 미노출. 기존 redaction 규칙 재사용. |
| Approved endpoint | shipper는 `POST /v1/ingest/enqueue`만 사용. URL에 credential 금지. |
| Verification posture | Hermes contract는 parser/verification unverified, hook install deferred로 명시. live POST/실세션 생성은 별도 승인 gate. |
| Compatibility | 기존 CLI/hook payload/enqueue wire format 미변경. 기존 4개 provider regression 금지. |
| Boundary tests | `test_client_boundary.py` 금지 import/문자열을 새 코드가 위반하지 않는다. |

## 사용자 시나리오

- Operator가 Hermes를 쓰는 환경에서 `dendrite transcript-capture --provider hermes`로
  세션 store pointer를 locator-only로 capture하고, 기존 spool/shipper로 neurons에
  안전한 pointer envelope를 보낼 수 있다.
- Maintainer가 provider를 추가/점검할 때 Hermes가 다른 provider와 동일한 식별 체계와
  spool/shipper 경로에 통합되어 있음을 확인한다.
- Reviewer가 `requirements.md`/`design.md`만 보고 Hermes가 왜 locator-only pointer로
  제한되는지(SQLite store + thin-client 경계)를 재구성할 수 있다.
- Security reviewer가 Hermes 경로에서 raw body/원경로/secret이 출력되지 않고 SQLite가
  열리지 않음을 test로 확인한다.

## 검증 완료 기준

- L0 경계 게이트: 기존 boundary/repo-instruction test가 통과하고 새 Hermes 코드가
  thin-client 금지 소유권(서버/brain/GC/RAGFlow, SQLite body 파싱)을 침범하지 않는다.
- L1 자동 검증: `uv run pytest -q` 통과. 최소 포함: provider contracts(hermes 포함),
  hermes capture payload, client boundary, transcript drain(hermes pointer + 기존
  regression).
- L2 로컬 런타임 스모크: `transcript-capture --provider hermes`가 locator-only JSON
  report를 만들고 raw body/원경로/credential을 출력하지 않는다. detector가 SQLite를
  열지 않는다. `transcript-drain --once`(기본)가 hermes를 `deferred`로 보류하고
  POST/quarantine 없이(network_used=false) 동작한다.
- L3 (deferred, 별도 승인): live `POST` synthetic canary, 실제 Hermes 세션 생성/열람.
- 증거에는 Hermes capture가 `locator_only`이고 public_summary에 원 DB 경로가 없으며,
  drain이 body 없이 pointer를 ship하고, 기존 4개 provider가 그대로 동작함이 포함된다.

## 허용 / 금지 범위

- 허용: Hermes locator-only capture, 파일 존재 detector, bounded local/fake-server
  smoke, JSON report 검증, pointer-only drain ship 검증.
- 별도 승인 필요: live `POST /v1/ingest/enqueue` canary, 실제 Hermes 세션 생성/열람,
  실제 production enqueue/ship.
- 금지: SQLite body open/read/query, raw transcript body 출력, private locator(원경로)
  출력, token/cookie/bearer 출력, `RAGFLOW_API_KEY` 취급, dataset_id/document_id 출력,
  `Ledger`/`TranscriptIngestWorker`/brain/server/GC/RAGFlow management 실행, Hermes DB
  write/delete, WAL checkpoint trigger.

## 미결정 항목 / 후속작업

- (cross-repo 후속) neurons가 ship을 받기 위한 계약: ingress allowlist에 pointer kind
  추가 + pointer-aware consumer(또는 SQLite 추출 owner). 이게 되면 dendrite는
  `--enable-hermes-ship`로 켠다. neurons repo 작업이라 이번 dendrite scope 밖.
- Hermes hook/extension API의 1차 자료 확인(없으면 hook install deferred 유지).
- 세션 단위 granularity(현재는 DB-file pointer; per-session 추출은 neurons 위임).
- 위 항목들은 dendrite producer 슬라이스 동작을 막지 않는다(defer-gate로 안전). 구현 중
  추가 SoT 변경이 필요하면 `grill-to-spec` 상류로 회귀한다.
