# Hermes Session Capture Reliability Requirements

## 승인 대상

- Source of truth: `requirements.md`
- Preview companion: 생성하지 않음. Markdown만으로 검토 가능.
- 작성 시각: 2026-07-04T13:53:05Z
- Repo: `dendrite`
- Phase: Requirements Discovery / approved

## 배경

현재 관측된 문제는 두 갈래로 분리한다.

1. **Dendrite 수정 범위**: Hermes agent 세션을 profile-aware하게 capture/spool/drain할 수 있도록 `dendrite`의 client-side 기능과 진단을 개선한다.
2. **별도 운영 복구 범위**: 현재 Mac `127.0.0.1:18080` tunnel이 stale ClusterIP를 향하고 있고, active drain LaunchAgent가 없어 non-Hermes provider 세션까지 pending에 쌓이는 문제는 별도 작업으로 다룬다.

이 문서는 1번, 즉 **Hermes 적재 관련 `dendrite` 수정 요구사항만** 정의한다. 2번은 이 요구사항의 precondition/diagnostic 대상일 수 있지만, 이 문서의 구현 scope로 합치지 않는다.

## 확인한 현재 사실

- `dendrite`는 이미 `hermes` provider를 지원한다.
  - `HermesSqliteSourceAdapter`가 Hermes `state.db`를 read-only/immutable로 열 수 있다.
  - `transcript-capture`, `transcript-migrate`, `transcript-drain` CLI가 존재한다.
- Hermes 세션 store는 profile별로 분리된다.
  - default: `~/.hermes/state.db`
  - metis: `~/.hermes/profiles/metis/state.db`
- 현재 local evidence 기준:
  - default Hermes config에는 `dendrite transcript-capture --provider hermes` hook이 있다.
  - `metis` profile config에는 해당 hook이 없다.
  - `~/.hermes/profiles/metis/state.db`에는 최신 메시지가 존재한다.
  - spool 내 `hermes` 항목은 매우 적다: acked 2건, pending 1건, quarantine 0건.
- 따라서 **metis Hermes 세션은 안정적으로 llm-brain에 적재되고 있다고 볼 수 없다.**

## 목표

Hermes agent 세션이 `dendrite` 경계를 지키면서 안정적으로 `neurons` ingress로 전달될 수 있게 한다.

핵심 목표는 다음이다.

- 모든 Hermes profile의 세션 store를 에이전트/profile 단위로 분리 식별한다.
- `default`, `metis`, `atlas`, `tyche` 같은 Hermes profile이 같은 `hermes` provider 아래에서 섞이지 않게 한다.
- profile별 `on_session_end` capture hook 설치/검증 계획을 제공한다.
- capture는 locator-only를 유지한다.
- drain 시점에만 Hermes SQLite를 read-only/immutable로 읽고 redaction 후 `conversation_chunk`로 보낸다.
- raw transcript, private path, raw session id, token/secret은 public 출력에 노출하지 않는다.
- 다른 provider pending/drain/tunnel 복구와 scope를 분리한다.

## 비목표 / 명시적 제외 범위

다음은 이 `dendrite` 수정 요구사항의 직접 scope가 아니다.

- Mac tunnel LaunchAgent 수정 또는 재시작.
- stale ClusterIP tunnel 복구.
- active drain LaunchAgent 생성/수정/재시작.
- non-Hermes provider backlog 전체 drain.
- `neurons` server-side ingest, CouchDB, Postgres, session-memory build, graph projection 수정.
- live GC, delete, disable, RetiredIndexBridge direct write.
- Hermes core 자체 수정.
- `~/.hermes/profiles/*/config.yaml` 자동 mutation. 단, 안전한 hook plan/diagnostic 출력은 포함한다.

## 기능 요구사항

### FR-1. Hermes profile-aware source discovery

`dendrite`는 Hermes default profile뿐 아니라 모든 named profile의 `state.db`도 profile-aware하게 다룰 수 있어야 한다.

- default profile source: `~/.hermes/state.db`
- named profile source: `~/.hermes/profiles/<profile>/state.db`
- capture request 또는 migration input은 어느 Hermes profile에서 온 세션인지 보존해야 한다.
- Hermes profile은 `hermes_profile` 또는 동등한 stable metadata로 public-safe하게 전달되어야 한다.
- `agent_id`/producer identity는 `hermes-<profile>-transcript-capture`처럼 profile별로 분리되어야 한다.
- 서로 다른 profile의 같은 raw session id가 같은 source로 섞이면 안 된다.
- session identity hash는 provider뿐 아니라 Hermes profile boundary를 반영해야 한다. 즉 서로 다른 profile의 같은 raw session id가 같은 `session_id_hash`로 충돌하면 안 된다.
- source path는 private locator로만 보관하고 public stdout/report에는 raw path를 출력하지 않는다.

### FR-2. Profile-aware capture hook plan

`dendrite provider hook-plan --provider hermes` 계열 진단은 profile별 hook readiness를 보여줘야 한다.

- default profile과 `~/.hermes/profiles/*`의 named profile을 enumerate해 각 profile의 hook 설치 여부를 aggregate로 표시한다.
- 출력은 profile name, readiness status, missing reason, recommended command shape 수준으로 제한한다.
- raw config 전체, raw private path, token/secret은 출력하지 않는다.
- hook plan은 기본적으로 non-mutating이어야 한다.
- 실제 config 수정은 별도 operator 승인과 별도 작업으로 남긴다.

### FR-3. Hermes on-session-end capture contract

Hermes `on_session_end` hook에서 들어오는 payload를 `dendrite transcript-capture --provider hermes`가 안정적으로 처리해야 한다.

- `session_id`가 있으면 해당 session만 capture 대상이 된다.
- `cwd` 또는 `workspacePaths`가 있으면 project label을 추론할 수 있어야 한다.
- profile-specific `state.db` locator를 명확히 줄 수 있어야 한다.
- locator가 없을 때는 현재 profile의 `HERMES_HOME` 또는 documented default를 사용하되, named profile을 default profile로 오인하면 안 된다.
- payload에 raw transcript body가 포함되면 현재 정책처럼 fail-closed 해야 한다.

### FR-4. Hermes migration/backfill dry-run

이미 존재하는 Hermes profile 세션을 재적재 또는 백필하기 전에 dry-run으로 안전하게 계수화할 수 있어야 한다.

- 모든 발견 가능한 Hermes profile에 대해 profile별 session count를 집계한다.
- backfill 대상은 profile 단위로 선택 가능해야 한다.
- 기본 live backfill은 전체 profile 일괄 실행이 아니라 bounded/profile-scoped 실행이어야 한다.
- raw session id, message body, private db path는 출력하지 않는다.
- `--limit` 같은 bounded smoke 옵션이 있어야 한다.
- migration은 capture request를 spool하는 단계까지만 수행하며 server-side memory build 책임을 갖지 않는다.
- 재실행 시 중복 위험을 사용자에게 명확히 표시한다.

### FR-5. Drain preflight visibility

Hermes capture가 정상이어도 ingress가 죽어 있으면 pending만 쌓인다. `dendrite`는 이 상태를 operator가 구분할 수 있게 해야 한다.

- `transcript-drain --once` 또는 별도 doctor는 ingress URL reachability를 aggregate로 표시해야 한다.
- HTTP timeout, connection refused, rejected, invalid JSON을 구분해 safe error class로 표시한다.
- raw URL에 credential이 포함된 경우 출력하지 않는다.
- ingress 장애는 `dendrite` source/capture 장애와 분리해서 보고한다.

### FR-6. Spool observability for Hermes

`dendrite`는 Hermes provider 항목이 pending/acked/quarantine 중 어디에 있는지 safe aggregate로 볼 수 있어야 한다.

- provider=`hermes` count by status.
- Hermes profile count by status.
- project count by status.
- newest pending/acked/quarantine timestamp.
- failure class aggregate.
- raw locator, raw session id, raw transcript body는 출력 금지.

### FR-7. Fail-closed privacy and safety

모든 Hermes 관련 출력과 wire payload는 기존 `dendrite` privacy boundary를 유지해야 한다.

- SQLite open mode는 read-only/immutable이어야 한다.
- WAL checkpoint 또는 Hermes DB write를 유발하면 안 된다.
- raw transcript body는 stdout/log/report에 출력하지 않는다.
- private path는 public output에 출력하지 않는다.
- token/cookie/bearer/API key/secret-like metadata는 fail-closed 한다.
- public payload에는 redacted `conversation_chunk`와 opaque/hash metadata만 포함한다.

### FR-8. Scope separation guard

Hermes 개선 작업이 non-Hermes backlog 복구와 섞이지 않게 해야 한다.

- `dendrite` 요구사항/테스트는 Hermes provider capture correctness를 다룬다.
- `dendrite`는 Hermes profile별 source identity와 payload metadata를 분리한다.
- `127.0.0.1:18080` tunnel stale, LaunchAgent enablement, non-Hermes backlog drain은 별도 runbook/운영 작업으로 분리한다.
- 단, `dendrite`는 해당 외부 문제가 있을 때 안전한 diagnostic status를 제공할 수 있다.

## 비기능 요구사항

| 항목 | 요구값 |
| --- | --- |
| Privacy | raw transcript, raw session id, private path, secret-like value 미출력 |
| DB safety | Hermes SQLite는 `mode=ro&immutable=1` 또는 동등한 no-write/no-checkpoint 방식만 허용 |
| Idempotency | 동일 source/session 반복 capture가 과도한 중복 write를 만들지 않도록 기존 content hash/idempotency 경계를 유지 |
| Blast radius | 기본 동작은 non-mutating doctor/plan/dry-run 우선 |
| Profile safety | default profile과 named profile을 혼동하지 않음 |
| Agent/profile separation | 모든 Hermes profile은 같은 provider 아래에서도 profile별 source/session/agent identity로 분리 |
| Backpressure | drain은 bounded tick 유지. 대량 backlog를 한 번에 쏟지 않음 |
| Testability | unit/CLI tests로 profile discovery, hook plan, migration dry-run, privacy redaction, drain error class를 검증 |
| Compatibility | 기존 `codex`, `claude`, `gemini`, `antigravity` capture/drain behavior를 깨지 않음 |

## 사용자 시나리오

### Scenario 1. metis Hermes 세션 capture readiness 확인

운영자는 `metis` profile의 Hermes 세션이 llm-brain 적재 대상인지 확인한다.

기대 결과:

- `metis` profile state DB 존재 여부와 session/message aggregate가 표시된다.
- `on_session_end` hook 설치 여부가 표시된다.
- 미설치라면 safe command plan이 표시된다.
- raw path나 raw session id는 표시되지 않는다.

### Scenario 2. 새 Hermes 세션 종료 후 capture spool 생성

Hermes `metis` 세션 종료 시 hook이 `dendrite transcript-capture --provider hermes`를 호출한다.

기대 결과:

- capture request가 private spool pending에 생성된다.
- request는 `provider=hermes`, profile/project metadata, hashed locator를 가진다.
- raw transcript body는 capture request에 없다.

### Scenario 3. drain 전 ingress 장애 구분

ingress tunnel이 stale이면 drain은 pending을 무한히 ack하지 않는다.

기대 결과:

- drain 결과는 `ingress_unreachable` 또는 timeout 계열 safe error로 끝난다.
- capture/source 문제와 ingress 문제를 구분한다.
- recoverable failure는 pending/retry로 남고 raw content는 출력하지 않는다.

### Scenario 4. Hermes historical backfill smoke

운영자는 기존 `metis` Hermes 세션 중 1개만 smoke로 spool/drain한다.

기대 결과:

- dry-run에서 count만 먼저 확인한다.
- `--limit 1`로 bounded spool이 가능하다.
- drain은 1건만 시도한다.
- server-side 적재 확인은 별도 운영 작업에서 수행한다.

## 수용 기준

- `dendrite` test suite에 Hermes profile-aware capture/migration/doctor 관련 테스트가 추가된다.
- `provider doctor` 또는 동등한 CLI가 default 및 named Hermes profile별 readiness를 raw private data 없이 보여준다.
- `transcript-capture --provider hermes`는 named profile state DB locator를 안전하게 받을 수 있다.
- `transcript-migrate --provider hermes`는 profile별 dry-run 및 bounded migration을 지원한다.
- capture/drain payload의 `session_id_hash`, `agent_id`, profile metadata가 profile 간 충돌 없이 분리된다.
- `transcript-drain --once`의 ingress 장애 결과가 source/capture 장애와 구분된다.
- 기존 non-Hermes provider tests가 regression 없이 통과한다.
- 문서 `docs/HERMES_PROVIDER.md`가 profile-aware 사용법과 scope separation을 반영한다.

## 결정된 항목

1. **적용 범위**: `dendrite`는 default profile과 모든 named Hermes profile을 발견/진단할 수 있어야 한다.
2. **저장/identity 경계**: 모든 Hermes profile은 에이전트/profile 단위로 분리 식별되어야 한다. default와 named profile의 session/source/agent identity가 섞이면 안 된다.
3. **설치 방식**: 이번 scope는 non-mutating plan only다. `dendrite`는 hook readiness와 recommended command shape를 출력하지만 `~/.hermes` config를 자동 수정하지 않는다.
4. **project label 정책**: `cwd`/`workspacePaths`에서 추론한 workspace를 `project`로 사용하고, Hermes profile은 `hermes_profile` 또는 동등한 별도 metadata로 보존한다.
5. **historical backfill 정책**: 전체 live backfill은 허용하지 않고, profile별 dry-run과 bounded smoke(`--limit`)만 이번 scope에 포함한다.

## 미결정 항목

없음.

## 승인 상태

`requirements.md`는 사용자 사전승인에 따라 승인된 source of truth다. HTML preview는 생성하지 않았다.
