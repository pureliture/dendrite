# Hermes Provider Capture Design Spec

## Overview

`dendrite`(Mac thin-client)에 Hermes agent를 capture 대상 provider로 추가한다.
Hermes 세션은 단일 SQLite store(`~/.hermes/state.db`)에 저장되므로, 기존 jsonl
provider와 달리 **locator-only pointer provider**로 통합한다. dendrite는 store의
locator(경로 handle)와 안전 metadata만 다루고 SQLite body는 절대 열거나 파싱하지
않는다. 실제 세션 본문 추출은 neurons(server/brain)의 책임이다. neurons가 아직 pointer
계약을 갖추지 않아, ship은 기본 비활성(**defer-gate**)이며 capture+spool까지만 동작한다
(아래 "개정: defer-gate" 참고).

## Requirements Reference

- Phase 1 source: `requirements.md`
- 핵심 요구:
  - Hermes를 canonical `hermes`로 식별 site 전 구간에 일관 등록.
  - locator resolver(명시 config -> `HERMES_HOME` -> 기본 `~/.hermes/state.db`) +
    존재 기반 detector(SQLite open/query 금지).
  - 기존 `locator_only` capture request schema 호환 envelope 생성.
  - approved ingress(`POST /v1/ingest/enqueue`)만 사용하는 pointer-only drain ship.
  - 기존 codex/claude/gemini/antigravity regression 금지.
  - server brain/session-memory build/GC/RAGFlow write 책임 미추가.

## Approach Proposal

### 선택안 A (추천): Locator-only pointer provider + 좁은 hermes 분기

Hermes를 다른 provider와 동일한 식별 체계로 등록하되, drain에서 SQLite body를 읽지
않고 locator pointer 문서만 ship하는 좁은 `provider == 'hermes'` 분기를 둔다.

- 장점: thin-client 경계 준수(파싱 위임), full path(identity->locator->spool->ship)
  실제 동작, 기존 4개 provider 경로 무변경 -> regression risk 최소.
- 단점: drain에 provider 분기 1개 추가. dendrite는 Hermes 본문을 ship하지 않음
  (의도된 제한; 본문 추출은 neurons).

### 선택안 B: `SOURCE_UNPROVEN_PROVIDERS`로만 staging

Hermes를 `SOURCE_UNPROVEN_PROVIDERS`에 넣어 locator 추출을 bypass(빈 locator)하고
source_status=`source_unproven`으로 둔다.

- 장점: 코드 변경 최소, 가장 보수적.
- 단점: 빈 locator라 envelope에 실제 session locator가 없고, drain이 unproven을
  permanent quarantine 처리하여 neurons로 **실제 ship되지 않음** -> 완료 기준
  "neurons로 보낼 수 있는 redacted envelope 생성"을 약하게만 만족. 채택 안 함.

### 선택안 C: dendrite에 SQLite transcript extractor 구현

dendrite가 `state.db`를 read-only로 열어 세션을 추출/redact해 body를 ship.

- 장점: 다른 provider와 ship 형태가 동일.
- 단점: SQLite open/query는 detector 금지 사항이고, transcript build는 neurons
  책임(thin-client 경계 위반). WAL checkpoint/락 리스크. 채택 안 함.

**결정: 선택안 A — 단, ship은 defer-gate.** 경계 준수 + 최소 regression. (general한
"parser-unverified면 pointer ship" 규칙은 YAGNI, 이번엔 명시적 hermes 분기로 한정.)

### 개정(구현/리뷰 중 발견 → 재논의): defer-gate

system-architecture 리뷰가 cross-repo 갭을 발견: neurons ingress validator
(`IngestJobValidator.DOCUMENT_KINDS`)는 closed allowlist이고 `session_pointer` kind가
없어, pointer를 ship하면 fail-closed(400)로 거부된다. neurons엔 pointer consumer도
없다. neurons는 server/brain repo라 이번 dendrite scope 밖.

따라서 ship을 **기본 비활성(defer-gate)**으로 개정한다:

- Hermes는 capture + spool까지만 기본 동작. drain은 hermes를 POST하지 않고 spool의
  `deferred` 상태로 보류한다(fail-closed quarantine 아님, network 미사용).
- pointer 빌더(`_build_hermes_pointer_document`)와 ship 경로는 코드에 남겨두되, 단일
  스위치 `--enable-hermes-ship`(기본 off)로만 활성화한다. neurons 계약이 갖춰지면 켠다.
- 이로써 dendrite는 정직한 producer 슬라이스가 되고, 거부될 데이터를 네트워크로 내보내지
  않는다. 필요한 neurons 계약은 아래 "Cross-Repo Contract"에 명시(구현은 후속작업).

## Architecture

```mermaid
flowchart LR
  H[Hermes state.db<br/>~/.hermes/state.db] -. 존재만 확인, open 안 함 .-> L
  subgraph dendrite [dendrite thin-client]
    CLI[cli.py<br/>transcript-capture --provider hermes] --> NR[normalize_provider_capture_request]
    NR --> L[_resolve_hermes_session_locator<br/>locator-only, stat만]
    L --> VR[validate_capture_request<br/>locator_only 강제]
    VR --> SP[(TranscriptCaptureSpool<br/>pending/processing/acked/quarantine)]
    SP --> DR[transcript_drain<br/>build_drain_document]
    DR -- "provider==hermes" --> PT[locator pointer doc<br/>body read 없음]
    DR -- "기존 4 provider" --> BD[redacted body doc<br/>변경 없음]
    PT --> IQ[IngressQueueClient]
    BD --> IQ
    IQ --> IT[IngressHttpTransport<br/>POST /v1/ingest/enqueue]
  end
  IT --> N[(neurons ingress<br/>SQLite 본문 추출은 여기 책임)]
```

### Module Boundaries

| 모듈 | Hermes 변경 | 책임 |
| --- | --- | --- |
| `provider_contracts.py` | `SUPPORTED_PROVIDERS += 'hermes'`; `build_default_provider_source_contracts()`에 hermes 계약 1건 추가; `_provider_config_plan` hermes 분기(설정 없음 -> `{}`, deferred) | provider 식별/계약/hook-plan |
| `providers/contracts.py` | `no_op_hook_response`/`normalize_provider_event` 허용집합에 `hermes` 추가; `_normalize_hermes_hook_event` 추가 + dispatch | hook payload 정규화 |
| `providers/__init__.py` + `providers/hermes.py` | `__all__ += 'hermes'`; `PROVIDER = 'hermes'` stub | provider 모듈 등록 |
| `transcript_capture.py` | `SUPPORTED_TRANSCRIPT_PROVIDERS += 'hermes'`; `_resolve_hermes_session_locator` 추가; `_extract_source_locator`/`_capture_event_type` hermes 분기; `_looks_like_provider_storage_path`에 `.hermes` 인지 추가 | locator-only capture 생성 |
| `transcript_drain.py` | defer-gate: 기본은 hermes를 `deferred`로 보류(POST 없음). `--enable-hermes-ship` 시 `build_drain_document`의 `provider=='hermes'` pointer-only 분기(body read 없음)로 ship | thin shipper |
| `transcript_capture.py` (Spool) | `deferred` parked 상태 추가(`defer()`/`deferred_count()`), active `depth_counts` 형태는 불변 | spool 상태기계 |
| `cli.py` | `hook-plan`/`transcript-capture` `--provider` choices에 `hermes` 추가 | CLI 노출 |
| `transcript_migrate.py` | **변경 없음**(jsonl glob 부적합) | 단일 SQLite store라 backfill 제외 |
| `ingress_transport.py` / `transcript_ingest.py` / `spool.py` | **변경 없음** | provider-agnostic 전송/영속 |

## Data Flow

### Hermes capture (locator-only)

```mermaid
sequenceDiagram
  participant Op as Operator
  participant CLI as transcript-capture
  participant N as normalize_provider_capture_request
  participant R as _resolve_hermes_session_locator
  participant V as validate_capture_request
  participant S as TranscriptCaptureSpool
  Op->>CLI: --provider hermes --stdin-json (payload)
  CLI->>N: provider=hermes, payload
  N->>R: locator 해석(명시키 -> HERMES_HOME -> 기본)
  R-->>N: DB 경로(존재 확인, open 안 함) 또는 no-source
  N->>N: locator_hash + version_hash(stat:mtime,size)
  N->>V: capture request(content_policy=locator_only, body 없음)
  V-->>N: schema ok(원경로/secret 미노출)
  N->>S: enqueue (파일 0o600)
  S-->>Op: JSON report (hash만, raw 없음)
```

### Hermes drain (defer-gate; ship only when enabled)

```mermaid
sequenceDiagram
  participant D as transcript_drain
  participant S as TranscriptCaptureSpool
  participant B as build_drain_document
  participant Q as IngressQueueClient
  D->>S: claim된 hermes request
  alt ship 비활성 (기본)
    D->>S: defer() -> deferred 상태 보류
    Note over D,S: POST 없음, quarantine 없음, network 미사용
  else --enable-hermes-ship
    D->>B: provider==hermes -> pointer doc(locator hash/version/metadata)
    Note over B: SQLite body read 없음
    B->>Q: enqueue_document(pointer doc) -> POST /v1/ingest/enqueue
  end
```

## Component Details

### `_resolve_hermes_session_locator(payload) -> str | ""`
- 입력: hook/CLI payload dict.
- 우선순위: payload의 명시 locator 키(`source_locator`/`transcript_path`/
  `transcriptPath`/`hermes_db_path` 등 기존 키 + hermes 전용 키) -> `HERMES_HOME`
  env -> 기본 `~/.hermes/state.db`.
- 동작: 경로 문자열 해석 + 파일 **존재 확인만**. SQLite open/connect/query 금지.
  존재하지 않으면 `""` 반환(상위에서 no-source/skip 처리, 날조 금지).
- 출력: 단일 경로 문자열. 기존 `_validate_locator_value`(단일 라인, 공백 토큰 금지,
  secret-shape 금지, <=4096) 통과 대상.
- version hash: 기존 `_source_locator_version_hash`(stat의 `mtime_ns:size` sha256)
  재사용 — body 미열람.

### Hermes `ProviderSourceContract`
- `provider='hermes'`, `hook_event`=Hermes session-end 의미의 명시 문자열.
- `source_locator_field`=resolver가 채우는 locator 필드명.
- `native_parser_status`/`verification_status`: **unverified**로 정직하게 표기
  (live smoke 미수행). antigravity 계약을 템플릿으로 하되 live-smoke 주장 금지.
- `source_status`: 실제 존재 확인된 locator 기준 ship-eligible 값(빈 locator인
  `source_unproven` 사용 안 함). 본문 미파싱은 `native_parser_status`와 drain pointer
  분기로 표현.
- `hook_install_status='deferred_not_installed'`, `evidence_hash='pending_probe'`
  (antigravity와 동일 sentinel), `unsupported_reason`에 "Hermes hook API 미확정 +
  SQLite store 본문 추출은 neurons 위임" 명시.
- `_provider_config_plan(hermes)`는 `{}` 반환(설치 config 없음, deferred).
- doctor/hook-plan 불변식 유지: network_used/mutation flag 모두 False, plan_only.

### Drain defer-gate + `build_drain_document` hermes pointer 분기
- `drain_transcript_spool_once(hermes_ship_enabled=False)`(기본): claim 후 provider가
  `hermes`이고 ship 비활성이면 `capture_spool.defer()`로 `deferred` 보류, `ship_deferred`
  카운트. POST/quarantine 없음, `network_used=False`. report status `deferred`.
- `--enable-hermes-ship` 시: 정상 경로로 `build_drain_document`가 `provider=='hermes'`
  분기 -> `source_locator.runtime_handle`을 **읽지 않고** locator pointer 문서 구성
  (provider, locator_hash, locator_version_hash, observed_at, redacted metadata,
  `content_kind='locator_pointer'`, kind=`session_pointer`) -> 기존 `IngressQueueClient.
  enqueue_document` -> approved path로 POST.
- Spool: `deferred` parked 상태 추가. `defer()`/`deferred_count()`. active `depth_counts`
  형태는 deferred를 제외해 불변(기존 assertion 무회귀).

### `_normalize_hermes_hook_event` + `_capture_event_type` hermes 분기
- payload의 Hermes hook event 이름을 canonical event(`session_end` 등)로 매핑.
- 불명 시 기존 fallback(`payload.get('event_type','session_end')`) 활용.
- `SAFE_PAYLOAD_FIELDS` allowlist 준수, `RAW_TRANSCRIPT_FIELDS` 포함 시 기존대로 거부.

### `_looks_like_provider_storage_path` `.hermes` 인지
- `('.hermes',)` 또는 `('.hermes','state.db')` 패턴을 provider storage로 인지해
  workspace/project 추론에서 제외(다른 provider storage 패턴과 동일 취급).

## Error Handling

| 시나리오 | 처리 |
| --- | --- |
| `state.db` 없음 | resolver `""` 반환 -> capture skip/no-source. 날조 금지, 비정상 종료 아님. |
| locator에 공백/secret-shape | 기존 `_validate_locator_value`가 거부(기본 경로엔 공백 없음). |
| payload에 raw transcript 필드 | 기존 `RAW_TRANSCRIPT_FIELDS` 검사로 `ValueError` (hermes 우회 금지). |
| SQLite 잠금/WAL | 파일을 열지 않으므로 해당 없음(설계상 회피). |
| drain 네트워크 실패 | 기존 `RECOVERABLE_ERROR_CLASSES` retry/quarantine 분류 그대로(비-hermes). |
| hermes ship 비활성(기본) | drain이 `deferred`로 보류, POST/quarantine 없음, `network_used=False`. |
| neurons가 pointer kind 거부 | ship 비활성이라 애초에 POST 안 함. 활성 시엔 기존 ingress reject 분류로 처리. |
| 미지원 provider 문자열 | 기존 fail-closed `ValueError` 유지. |
| 알 수 없는 hermes hook event | `_capture_event_type` fallback으로 안전 기본 event. |

## Testing Strategy

- 프레임워크: `uv run pytest -q` (worktree 루트). 신규 `tests/test_hermes_capture_payload.py`는
  `tests/test_antigravity_capture_payload.py`를 템플릿으로 한다.
- 필수 케이스:
  1. 식별: hermes가 `SUPPORTED_PROVIDERS`/`SUPPORTED_TRANSCRIPT_PROVIDERS`/
     `no_op_hook_response`/`normalize_provider_event`/contract 목록/CLI choices에
     모두 등록. `test_provider_contracts.py`의 set-equality 단언을 hermes 포함으로 갱신.
  2. locator-only capture: hermes payload -> capture request가 `content_policy==
     'locator_only'`, body 없음, `public_summary`/직렬화에 원 DB 경로 미포함, spool
     파일 0o600.
  3. detector 안전성: resolver/detector가 SQLite를 open/connect하지 않음(파일 미열람
     검증 — 예: read 호출/connect monkeypatch가 호출되지 않음).
  4. raw 거부: `RAW_TRANSCRIPT_FIELDS` 포함 payload는 `ValueError`.
  5a. drain defer(기본): hermes request가 POST 없이 `deferred`로 보류, status
     `deferred`, `ship_deferred_count==1`, quarantine 0, `network_used=False`.
  5b. drain ship(`--enable-hermes-ship`): hermes가 body read 없이 pointer 문서로
     approved endpoint에 enqueue. body+metadata에 원경로/본문/raw session id 미포함,
     `content_kind=locator_pointer`.
  6. regression: codex/claude/gemini/antigravity가 그대로 식별되고 drain에서 body를
     redact해 ship(기존 동작 불변). active `depth_counts` 형태 불변.
  7. client boundary: 신규 파일이 `FORBIDDEN_IMPORT_ROOTS`/`FORBIDDEN_SOURCE_FRAGMENTS`
     (docker, RAGFLOW_API_KEY, brain_query, Ledger 등) 미위반.
  8. approved endpoint: shipper가 고정 path만 사용, URL credential 거부 동작 유지.
- evidence: 위 test green + L2 로컬 smoke(`transcript-capture --provider hermes`가
  hash만 담은 JSON report 생성, 원경로/raw 미출력).

## TDD Strategy

code-changing work이므로 red -> green -> refactor를 기본으로 한다.

- 각 milestone은 동작 test를 먼저 추가하고 fail(red)을 확인한 뒤 production code로
  green을 만든다. 식별/capture/drain/regression 순으로 red 테스트를 선행한다.
- docs/sample-config milestone(M4의 문서 부분)은 no-test-seam 예외로, 대체 evidence는
  렌더된 문서 내용과 boundary test 통과로 갈음한다.

## Milestones

agentic-execution이 act->observe->adjust로 소비할 evidence 단위. 순서는 권장이며 각
단위는 done 정의와 기대 evidence를 가진다.

- M0: Red 테스트 선작성 — 식별/locator-only capture/detector 안전/drain pointer/
  regression에 대한 실패 테스트 작성. done: 새 테스트가 의도대로 red.
- M1: Provider identity green — 6개 식별 site + `providers/hermes.py` stub + contract
  추가. done: 식별 테스트 green, `test_provider_contracts` set-equality 갱신 반영.
- M2: Locator + detector + capture green — `_resolve_hermes_session_locator`,
  capture 분기, `.hermes` storage 인지. done: locator-only capture/detector 안전/raw
  거부 테스트 green, SQLite 미열람 증명.
- M3: Drain defer-gate + regression green — drain이 hermes를 `deferred`로 보류(기본),
  `--enable-hermes-ship` 시 pointer ship. done: defer 테스트 + enabled ship 테스트 green
  + 4개 provider regression green + active depth_counts 형태 불변.
- M4: CLI + hook-plan deferred + docs/sample — CLI choices, deferred hook-plan,
  enable 방법/안전 경계/샘플 설정 문서화. done: CLI로 hermes 도달 가능, hook-plan이
  non-mutating deferred plan 출력, 문서/샘플 존재 + boundary test 통과.
- M5: Full local verification — `uv run pytest -q` 전체 green + L2 로컬 smoke evidence
  (raw/원경로/credential 미출력 확인). done: 전체 통과 + smoke JSON report 증거.

## Cross-Repo Contract (neurons enable precondition)

dendrite의 hermes ship(`--enable-hermes-ship`)을 켜기 전에 neurons(server/brain repo)가
갖춰야 할 계약. **구현은 이번 dendrite scope 밖, cross-repo 후속작업으로 추적.**

- neurons ingress allowlist(`IngestJobValidator.DOCUMENT_KINDS`)에 pointer kind 추가
  (`session_pointer` 또는 합의된 명칭). 현재는 closed allowlist라 거부됨.
- pointer-aware consumer: `content_kind=locator_pointer` 문서를 transcript chunk로
  오인 저장하지 않고, 적절히 라우팅(또는 SQLite 본문 추출 owner 지정). 현재
  `CouchDBDeliveryBackend`는 모든 payload를 conversation_chunk로 취급.
- 계약 형태 결정: `agent_knowledge_document.v2` 재사용 vs distinct `*_pointer.v1` 스키마.
  distinct 쪽이 cross-repo 계약을 명시적으로 만든다(권장 검토).
- 위 계약이 서면/구현되면 dendrite는 `--enable-hermes-ship`로 ship을 켜고 end-to-end
  smoke(승인된 canary)로 검증.

## Open Questions

- Hermes hook/extension API 1차 자료(없으면 hook install deferred 유지, capture는
  explicit invocation으로 동작).
- pointer 계약 스키마/kind 명칭의 cross-repo 합의(위 Cross-Repo Contract).
- 세션 단위 granularity(현재 DB-file pointer; per-session 추출은 neurons 위임).

## Review Feedback Log

- (초안) grill-to-spec 자문자답 + 7개 sonnet 리서치 + Hermes 공식 문서 기반 작성.
- (구현 후 리뷰) code-simplifier(opus): locator resolver 중복 가드를 helper로 추출(동작
  보존). codebase-architecture(opus): SOUND, registry 통합은 6번째 provider 시점까지
  YAGNI, drain 분기는 올바른 seam — 반영(현행 유지).
- (system-architecture 리뷰, opus): **cross-repo 갭 발견** — neurons ingress allowlist가
  `session_pointer`를 거부하고 pointer consumer 부재. 직접 검증함(allowlist 8종에 없음).
- (재논의 결정) 사용자와 재논의 → **defer-gate** 채택: ship 기본 비활성, drain은 hermes를
  `deferred`로 보류(POST/quarantine 없음), `--enable-hermes-ship`로만 활성. neurons 계약은
  Cross-Repo Contract로 문서화 + 후속작업 분리. SoT(requirements/design) 갱신은 본 회귀에서 수행.
  사용자 사전 승인에 따라 design 완성 즉시 agentic-execution으로 핸드오프.
