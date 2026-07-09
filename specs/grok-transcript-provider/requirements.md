# Grok Build Transcript Provider — Dendrite Requirements

## 승인 대상

- Source of truth: `specs/grok-transcript-provider/requirements.md`
- Preview companion: `requirements.html` (필요 시)
- Companion (server, 별도 완료): `neurons` repo `specs/grok-transcript-provider/` (있을 경우)
- 승인 상태: **approved** (사용자 pre-approve + research gate; live 이벤트 = `Stop`)
- Research evidence: `docs/live/grok-build-research-gate.md`

## 배경

`dendrite`는 Mac thin-client로 provider hook → locator-only spool → `transcript-drain` →
`POST 18080`까지만 책임진다. **Grok Build**(`grok` CLI, 기본 모델 `grok-build`)는 Hermes와
별도 harness이며 세션을 `GROK_HOME`(기본 `~/.grok`) 아래에 저장한다. 현재 allowlist에는
`grok`가 없어 Build 세션은 brain ingest 대상이 아니다.

이 문서는 **dendrite 측 요구사항만** 정의한다. server 파싱·lane·session-memory는
`neurons` companion 범위이며, **dendrite 완료 조건에 묶지 않는다**.

## 구현 전 필수 리서치 (정확성 게이트)

상세 산출물: **`docs/live/grok-build-research-gate.md`** (2026-07-09, sanitized).

| 항목 | 상태 / 출처 |
| --- | --- |
| 설치된 Grok Build 버전 | **완료** — `grok 0.2.93 (f00f96316d4b) [stable]` |
| 번들 사용자 문서 | **완료** — `10-hooks.md`, `17-sessions.md`, `05-configuration.md` |
| 세션 저장 레이아웃 | **완료** — `sessions/<encoded-cwd>/<session-id>/`, SoT `updates.jsonl` |
| `summary.json` 세션 id | **완료** — top-level id 없음; `info.id` + **디렉터리명** 사용 |
| `sessionUpdate` 타입 목록 | **완료** — `user_message_chunk`, `agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `tool_call_update`, `hook_execution`, `turn_completed` 등 |
| 훅 이벤트 문서 | **완료** — `Stop`=턴 종료, `SessionEnd`=세션 종료 (둘 다 1급) |
| headless `grok -p` live smoke | **완료** — `SessionStart`+`Stop` 발화, **`SessionEnd` 미발화** |
| Stop stdin | **완료** — keys: `cwd`, `hookEventName`, `promptId`, `reason`, `sessionId`, `timestamp`, `transcriptPath`, `workspaceRoot`; `transcriptPath` basename=`updates.jsonl` |
| SessionStart stdin | **완료** — no `transcriptPath`; has `sessionId`, `cwd`, `workspaceRoot` |
| Interactive TUI `SessionEnd` | **미실시** (대화형 quit 필요) |
| upstream 문서 불일치 | 로컬 번들 기준 고정; 공식 URL 차이는 design 시 재확인 |

raw transcript·전체 private path는 커밋하지 않는다.

## 질문-답변 흐름

### Q: provider 이름은 `grok-build`인가 `grok`인가?

`grok`이다. `grok-build`는 **모델 ID**이며 ingest identity는 harness 단위로 `grok` 하나로
통일한다. 모델 메타는 server 측에서 `summary.json` 등으로 보강할 수 있다(neurons scope).

### Q: Hermes atlas(xai-oauth)와 중복 구현인가?

아니다. Hermes 경로는 `provider=hermes` + profile이다. 이 작업은 **독립 Grok CLI** 전용이다.

### Q: live와 migrate 중 무엇이 정본인가?

**live capture 경로가 정본**이다.  
`normalize_provider_capture_request` → spool → `transcript-drain` / `adapter_for` 가 canonical이다.
migrate는 **동일 경로를 재사용**하고, 차이점은 세션 **열거/트리거**뿐이다.
이는 기존 codex/claude/gemini/antigravity/hermes migration 철학과 같다.

### Q: 기존 migrate 방식과 다른가?

**ship 경로는 다르지 않다.**  
migrate 모듈은 원래 live `transcript-capture`와 같이 locator-only request만 spool한다.
Hermes만 열거가 SQLite 세션 단위로 달랐고, Grok은 레이아웃이
`sessions/<cwd-group>/<session-id>/updates.jsonl` 이라 **열거 단위·locator resolve**만
provider-specific이다. 새 ingress wire format이나 migrate-only pipeline을 만들지 않는다.

### Q: migrate 후 같은 세션에 live `Stop`이 오면?

**codex/claude와 동일한 공유 멱등 규칙**을 쓴다. 별도 Grok 정책을 invent하지 않는다.

- `request_id` identity에 `locator_version_hash`(파일 `mtime_ns:size`)가 포함된다.
- 파일이 불변 → 동일 request → spool 중복 금지.
- 파일이 갱신 → 새 request → 재 ship.
- migrate re-run과 live 재발화가 같은 규칙을 공유한다.

### Q: live capture는 어떤 훅 이벤트인가?

기본은 **`Stop`**이다 (research gate 실측 반영, 2026-07-09 확정).

문서 (`10-hooks.md`):

- `Stop` = agent turn ends (completed / cancelled / error)
- `SessionEnd` = session ends (문서 1급이나 headless에서 미발화)

headless 실측 (`grok -p … --output-format json`, 반복):

| 이벤트 | 발화 |
| --- | --- |
| SessionStart | 예 |
| Stop | **예** (`reason` 예: `end_turn`; `transcriptPath` → `updates.jsonl`) |
| SessionEnd | 아니오 (프로세스 정상 종료 후에도 관측 없음) |

결정 근거:

- headless/`-p` live를 커버하려면 `Stop`이 실제 발화 이벤트다.
- 턴마다 올 수 있으나 **codex/claude와 동일한** `locator_version_hash`(mtime/size) identity로
  동일 파일이면 중복 spool 금지, 파일이 커지면 재 ship — 별도 Grok 쓰로틀 invent 안 함.
- codex가 Stop을 session_end 계열로 매핑하는 기존 패턴과 정합.
- `SessionEnd`는 이번 기본 plan에 **넣지 않는다** (TUI 전용 보강이 필요하면 후속 FR).
- Interactive TUI에서의 SessionEnd 발화는 미검증이며 이번 완료 조건이 아니다.

### Q: locator는 무엇인가?

provider-native SoT 파일 **`updates.jsonl`의 절대 경로**(private runtime handle)이다.

- Stop payload의 **`transcriptPath`** 를 우선 사용 (실측: basename=`updates.jsonl`, 존재 확인됨;
  symlink 거부 정책 유지).
- 없거나 검증 실패 시 **`sessionId`(+ `GROK_HOME`)로 resolve**.
- `17-sessions.md`는 `updates.jsonl`을 resume/restore 권위 로그로 명시.

### Q: 기존에 이미 디스크에 있는 Grok 세션은?

`transcript-migrate --provider grok`로 **과거 세션 bulk backfill**을 지원한다.
live와 **동일한** locator-only capture request·동일 drain 경로를 재사용한다.

### Q: dendrite 완료에 neurons 파서가 필요한가?

**아니다 (dendrite 단독 완료).**  
provider 등록 + live/migrate 경로 + unit/smoke까지면 dendrite 완료로 본다.
opaque `updates.jsonl` → redacted `conversation_chunk` enqueue로 충분하다.
neurons native parser·lane 품질은 companion 작업이며 dendrite done 조건에 묶지 않는다.

### Q: hook 자동 설치는?

하지 않는다. `hook-plan`은 non-mutating plan만. 실제 `~/.grok/hooks` 쓰기는 승인·smoke 이후
operator 작업이다.

## 기능 요구사항

### FR-D1 — Provider 등록

- CLI·contract·capture normalization에 **`grok`** provider를 추가한다.
- `SUPPORTED_PROVIDERS` / `SUPPORTED_TRANSCRIPT_PROVIDERS` / `provider hook-plan` choices에
  포함한다.
- 기존 provider(claude, codex, gemini, antigravity, hermes) 동작을 깨지 않는다.

### FR-D2 — Provider source contract

- `build_default_provider_source_contracts()`에 `grok` contract 1건을 추가한다.
- contract에 **설치 버전 evidence**, hook 이벤트(**`Stop`**), locator 필드 정책,
  `parser_version`, verification 상태를 기록한다.
- 초기 상태는 `source_locator_unverified` / `deferred_not_installed`를 허용하되,
  **hook install은 source smoke 검증 전 차단**한다(기존 `blocked_source_unproven` 패턴).

### FR-D3 — Locator-only capture (live)

- `normalize_provider_capture_request("grok", ...)`가 훅 stdin JSON을 받아
  **transcript 본문 없이** capture request를 생성한다.
- `Stop` 이벤트(및 동등 `hookEventName`/`stop`)를 `session_end` 계열 capture로 매핑한다
  (기존 codex Stop 매핑 패턴과 정합; design에서 필드명 고정).
- `session_id`(또는 동등 필드)와 locator(`updates.jsonl`)를 request에 담는다.
- locator 우선순위: payload `transcriptPath`/`transcript_path` → sessionId resolve.
- `session_id_hash` seed는 기존 패턴을 따른다(예: `grok:<sessionId>` — design에서 고정).
- project 라벨은 payload의 workspace/cwd 기반 canonicalize를 우선한다(기존 provider와 동일
  정책).
- 이 normalize 결과가 **live·migrate 공통 정본**이다.

### FR-D4 — Source locator resolution

- `GROK_HOME`(미설정 시 `~/.grok`) 아래 `sessions/`에서 `sessionId`에 해당하는
  **`updates.jsonl`** 경로를 resolve한다.
- cwd URL-encoding 그룹 디렉터리명과 불일치해도 **session id 기준 탐색**으로 실패를 줄인다.
- symlink locator는 거부한다(기존 locator 정책과 동일).
- resolve 실패 시 빈 locator로 조용히 성공하지 않는다(검증·quarantine 분류 가능).

### FR-D5 — Thin shipper (drain)

- `adapter_for("grok")`는 기본적으로 **jsonl opaque read**로 `updates.jsonl` 전체를
  redact·bound하여 `conversation_chunk` body를 만든다(1단계; neurons 파서 비의존).
- drain 메타데이터의 `source.provider`는 **`grok`**이다.
- 새로운 ingress wire format을 invent하지 않는다.
- live 단건과 migrate spool 항목이 **동일 drain 경로**를 탄다.

### FR-D6 — Hook plan (non-mutating)

- `dendrite provider hook-plan --provider grok`가 `~/.grok/hooks/*.json` 대상
  **계획만** 출력한다. 기본 이벤트는 **`Stop`**.
- planned argv는 `transcript-capture --provider grok --stdin-json --non-fatal` 패턴을
  따른다.
- **실제 hook 파일 쓰기는 승인·source smoke 이후** operator 작업으로 남긴다
  (자동 install 기본 금지).

### FR-D7 — 기존 세션 bulk migration

- `transcript-migrate`에 **`grok`** 를 추가한다(`MIGRATION_PROVIDERS`).
- 기본 source root: `$(GROK_HOME)/sessions` (override: `--source-root grok=/path`).
- 열거 단위: **세션당 1개** — 각 세션 디렉터리의 `updates.jsonl`만
  (`chat_history.jsonl` 등 다른 jsonl은 열거 대상 아님; 파일 없으면 skip·집계).
- 세션 식별: **세션 디렉터리명** 우선; 보조로 `summary.json` → `info.id`
  (top-level `id`/`session_id` 필드 없음 — 리서치 확정).
- request는 **FR-D3 normalize와 동일 schema**로 만든다(migrate-only schema 금지).
- `--dry-run`: spool 없이 **세션 수·스킵 사유 카운트만** 보고한다.
- `--limit N`: 스모크용 상한.
- **re-run 멱등**: codex/claude와 동일한 request identity / `locator_version_hash` 규칙.
  동일 locator·동일 version hash에서 duplicate spool 금지; 파일 갱신 시 재 ship 허용.
- migration report에 raw path·session id·transcript 내용을 출력하지 않는다(카운트만).

### FR-D8 — Provider doctor / readiness

- `provider doctor` summary에 `grok` contract 상태가 포함된다.
- optional: `GROK_HOME` 존재·`sessions/` 하위 `updates.jsonl` 개수(카운트만)를 readiness
  힌트로 노출한다(내용 읽기 없음).

## 비기능 요구사항

| 항목 | 요구값 |
| --- | --- |
| Thin-client boundary | Ledger, session-memory build, GC, RAGFlow write 추가 금지 |
| Locator-only hooks | 훅 payload에 transcript 본문 필드 금지(기존 `RAW_TRANSCRIPT_FIELDS` 정책) |
| Secret / path redaction | stdout·report·public enqueue에 raw private path·transcript 미포함 |
| Compatibility | 기존 CLI·wire·다른 provider migration 무회귀 |
| Capture identity | codex/claude와 공유하는 normalize/request_id/`locator_version_hash` 규칙 재사용 |
| Tests | provider contract, locator resolve, migrate enumerate, capture normalize 단위 테스트 |
| Boundary tests | `test_client_boundary`, `test_repo_instructions` 통과 |

## 인터페이스 계약 (neurons와의 경계)

dendrite가 보장하는 최소 계약:

| 필드 | 값 |
| --- | --- |
| `provider` | `grok` |
| locator kind | `provider_transcript_source` → `runtime_handle` = `updates.jsonl` path |
| shipped document | `conversation_chunk` (redacted body, opaque jsonl 1단계) |
| private spool `session_id` | Grok session UUID (drain 시 server/adapter 식별용) |

neurons native parser 품질·`PROVIDER_LANES`는 neurons requirements(FR-N*)가 정의한다.
**opaque ship으로 enqueue 가능한 것이 dendrite 완료 기준**이다. neurons companion은 별도
완료 트랙이다.

## 범위 제외

- Grok plugin marketplace 훅 배포·서명
- HTTP hook 원격 endpoint 운영
- neurons server 파서·CouchDB lane 구현(companion)
- `LIVE_CUTOVER_PROVIDERS` 정책 변경(neurons)
- Hermes `provider=hermes` 경로 변경
- 기본 live plan에 `SessionEnd` 포함 (이번 scope 제외; 후속 FR)
- hook 파일 자동 install
- migrate-only 또는 live-only 전용 ship pipeline

## 사용자 시나리오

1. Maintainer가 리서치 게이트 산출물(`docs/live/grok-build-research-gate.md`)을 인용해
   design을 작성한다.
2. Operator가 훅 없이 `transcript-capture` + 수동(또는 Stop 형태) payload로 1세션 smoke →
   spool → drain을 검증한다(live 경로 정본 증명).
3. Operator가 `transcript-migrate --provider grok --dry-run`으로 기존 세션 수를 확인한다.
4. Operator가 migrate → drain으로 과거 세션을 동일 경로로 적재한다.
5. (승인·source smoke 후) Operator가 `~/.grok/hooks`에 **`Stop`** 훅을 설치해 live capture를 켠다.

## 검증 완료 기준 (dendrite)

- [x] 리서치 게이트 산출물 존재 (`docs/live/grok-build-research-gate.md`)
- [x] live 훅 이벤트 = **`Stop`** 으로 requirements에 확정
- [ ] `uv run pytest -q` green (신규 grok 테스트 포함)
- [ ] `dendrite provider doctor`에 `grok` contract 노출
- [ ] `dendrite provider hook-plan --provider grok`가 non-mutating **`Stop`** plan 출력
- [ ] 로컬 실측: 기존 `~/.grok/sessions/**/updates.jsonl` ≥1건에 대해 migrate dry-run
      카운트 > 0 (또는 세션 없음을 문서화)
- [ ] locator resolve + drain 1건 smoke (ingress endpoint는 operator 환경; 실패 시
      `ingress_unreachable` 분류만으로 pytest·unit 증명 가능)
- [ ] neurons native parser 완료는 **dendrite 완료 체크리스트에 포함하지 않음**

## 미결정 항목 (design / 후속; 승인 비차단)

- Interactive TUI quit 시 `SessionEnd` 발화 여부 (기본 plan 밖; 후속 보강 후보)
- multi-turn Stop 빈도·drain 부하 운영 관찰 (identity 규칙으로 1차 제어)
- `_capture_event_type("grok", Stop)` 및 payload 키 별칭의 design 고정 세부
