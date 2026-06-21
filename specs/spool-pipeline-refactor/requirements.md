# Spool Pipeline Refactor Requirements

## 승인 대상

- Source of truth: `requirements.md`
- Preview companion: `requirements.html`
- 승인 상태: approved by user autopilot directive

## 질문-답변 흐름

### Q: 어떤 일을 하려는가?

`dendrite`의 2차 리팩토링으로, local spool/pipeline 코드를 더 깊은 인터페이스와 명확한 전송 경계로 정리한다.

### Q: 어떤 실행 방식으로 진행할 것인가?

- 요구사항과 설계는 `grill-to-spec`으로 작성한다.
- `requirements.md` 승인 전에는 `design.md`를 만들지 않는다.
- `design.md` 생성 후 `codebase_architecture_manager`와 `system_architecture_manager`로 멀티에이전트 리뷰를 수행한다.
- 리뷰 반영 후 단일 goal을 설정하고 장기 autopilot 작업으로 구현한다.
- 구현은 `agentic-execution` 루프와 `harnesskit-tdd`의 red-green-refactor 규율을 따른다.

### Q: 현재 설계안에서 어떤 후보가 있는가?

- Candidate 1: spooling layer 정리.
- Candidate 2: provider modules 정리.
- Candidate 3: spooling/shipping pipeline 통합.

### Q: 현재 평가에서 무엇이 확정됐는가?

- Candidate 1은 가치가 있으나, 도메인 wrapper를 제거하는 식의 과격한 통합은 피해야 한다.
- Candidate 2는 기능적 leverage가 낮아 이번 장기 goal의 기본 scope에서 제외한다.
- Candidate 3은 장기 가치가 크지만 retry/quarantine/error taxonomy 계약이 먼저 명확해야 한다.
- `dendrite`의 boundary는 계속 `provider hook -> locator-only spool -> thin shipper -> POST 18080`로 유지한다.

### Q: 이번 단일 장기 goal의 완료 범위는 어디까지인가?

리팩토링 구현, 로컬/런타임 검증, 프로덕션 검증까지 모두 완결한다. 단순 코드 정리나 테스트 통과에서 멈추지 않고 실제 운영 표면에서 동작이 확인되어야 한다.

### Q: production verification은 어느 강도까지 허용하는가?

Phased Gate로 진행한다. goal의 기본 완료 범위는 read-only production-adjacent audit와 local/fake-server synthetic smoke다. 실제 `POST /v1/ingest/enqueue`를 live endpoint로 보내는 synthetic canary는 별도 승인 gate가 endpoint, target profile, payload shape, no-retention/rollback 기대값을 명시할 때만 수행한다. 실제 provider session을 발생시키거나 raw/private runtime locator를 다루는 검증은 별도 승인 gate 없이는 수행하지 않는다.

### Q: Candidate 3은 이번 goal에 어느 수준까지 포함하는가?

Spool + Shipping Policy 범위까지 포함한다. 즉 retry/quarantine/unreachable/error taxonomy와 전송 client 경계를 정리한다. 다만 payload 변환 전체를 단일 generic shipper로 흡수하는 Full Pipeline Unification은 이번 goal의 기본 범위에서 제외한다.

### Q: `JsonFileSpool` 이름과 API는 어떻게 다루는가?

기존 import compatibility는 유지한다. 다만 호출부가 low-level 파일 조작을 직접 사용하지 않도록 domain wrapper 중심으로 이동하고, `JsonFileSpool`은 내부 영속화 helper 성격으로 명시한다.

### Q: spec/review artifact는 어디에 남기는가?

이번 branch/worktree의 작업 SoT로 유지한다. 별도 사용자 승인 없이 장기 공식 문서나 doc registry로 승격하지 않는다.

## 기능 요구사항

- 스풀 영속화의 파일 시스템 세부사항을 호출부에서 더 적게 알도록 정리한다.
- domain-facing spool API는 enqueue, claim, ack, quarantine, requeue 같은 상태 전이 중심이어야 한다.
- `TranscriptCaptureSpool`과 `FileBackedIngressOutbox`의 도메인 의미는 보존한다.
- 전송 경계는 `POST /v1/ingest/enqueue`로 유지한다.
- retry, quarantine, unreachable 처리의 의미가 transcript drain과 RAG ingress outbox 사이에서 일관되게 설명 가능해야 한다.
- transcript drain과 RAG ingress outbox의 payload format 차이는 보존한다. 이번 goal은 payload 변환 전체의 generic 통합을 요구하지 않는다.
- provider hook, transcript locator, source ref, migration command의 public CLI/API 호환성을 깨지 않는다.
- raw transcript body, private locator, token, credential은 stdout/report/public metadata로 출력하지 않는다.
- 구현 완료 후 local runtime smoke를 통해 관련 CLI/hook/drain 경로가 실제로 실행되는지 확인한다.
- 구현 완료 후 read-only production-adjacent audit와 local/fake-server synthetic smoke로 `dendrite` client seam에서 리팩토링된 경로가 기능함을 증명한다.
- 실제 provider session을 새로 발생시키거나 private runtime locator를 직접 열람하는 검증은 별도 승인 없이는 하지 않는다.

## 비기능 요구사항

| 항목 | 요구값 |
| --- | --- |
| Worktree isolation | 모든 파일 변경은 dedicated branch/worktree에서 수행한다. `main`/`master`에는 직접 쓰지 않는다. |
| Source of truth | Phase 1은 `requirements.md`, Phase 2는 `design.md`가 SoT다. HTML은 preview일 뿐이다. |
| TDD | 구현은 behavior test를 먼저 추가하고 fail을 확인한 뒤 production code를 작성한다. |
| Verification | 각 milestone은 구체적 evidence가 있어야 done 처리한다. |
| Runtime validation | 단위 테스트 외에 CLI/hook/drain runtime smoke evidence를 확보한다. |
| Production validation | production-adjacent 검증은 `dendrite` client seam에서 승인된 방식으로만 수행하고, raw transcript/locator/credential을 출력하지 않는다. Live `POST` synthetic canary는 별도 승인 gate가 있을 때만 수행한다. |
| Boundary | `dendrite`는 Mac thin-client이며 server/brain/GC/RAGFlow credential ownership을 갖지 않는다. |
| Compatibility | 기존 CLI, hook payload, enqueue payload wire format은 변경하지 않는다. |
| Review | spec 생성 후 `codebase_architecture_manager`, `system_architecture_manager` 리뷰를 받고 반영한다. |
| Documentation promotion | spec/review artifact는 작업 SoT로만 유지하고, 별도 승인 없이 공식 문서/registry로 승격하지 않는다. |

## 사용자 시나리오

- Maintainer가 spool 관련 동작을 바꿀 때 symlink, chmod, temp file, duplicate detection 같은 low-level 규칙을 여러 도메인 모듈에서 추적하지 않아도 된다.
- Operator가 `transcript-drain` 또는 RAG ingress outbox 전송 실패를 볼 때 retry 가능 실패와 quarantine 대상 실패를 같은 언어로 이해한다.
- Agent가 장기 refactor를 이어받아도 `requirements.md`와 `design.md`만 보고 scope, non-goals, milestone evidence를 재구성할 수 있다.
- Operator가 실제 provider hook 또는 approved production-adjacent path를 통해 변경된 경로가 운영에서 동작하는지 확인한다.

## 검증 완료 기준

- 구현 완료는 다음 4개 층을 모두 통과해야 한다.
- L0 경계 게이트: `uv run python -m dendrite --show-boundary`가 `provider hook -> locator-only spool -> thin shipper -> POST 18080`를 출력하고, repo instruction/boundary tests가 금지 소유권 침범을 잡아야 한다.
- L1 자동 검증: `uv run pytest -q`가 통과해야 하며, 최소한 client boundary, provider contracts, spool, transcript drain, transcript migrate, RAG ingress outbox 관련 tests가 포함되어야 한다.
- L2 로컬 런타임 스모크: `transcript-capture`, `transcript-migrate --dry-run`, `transcript-drain --once`가 JSON report를 생성하고 raw transcript/body/locator/credential을 출력하지 않아야 한다.
- L3 production-adjacent 검증: read-only audit와 local/fake-server synthetic smoke를 수행한다. Live `POST /v1/ingest/enqueue` synthetic canary, 실제 provider session 생성, real production enqueue/ship는 별도 승인 gate에서만 수행한다.
- 상태 전이 evidence는 network failure가 retry 가능한 상태로 남고, 영구 실패가 quarantine으로 이동하며, 성공 케이스가 ack 또는 queued 결과로 관측됨을 포함해야 한다.

## 허용 / 금지 범위

- 허용: locator-only capture, bounded dry-run, read-only production-adjacent audit, local/fake-server smoke, JSON report 검증, spool depth/status 검증.
- 별도 승인 필요: live `POST /v1/ingest/enqueue` synthetic canary, 실제 provider session 생성, 실제 production enqueue/ship, 승인된 synthetic canary가 아닌 운영 데이터 기반 검증.
- 금지: raw transcript body 출력, private locator 출력, token/credential 출력, `RAGFLOW_API_KEY` 취급, dataset_id/document_id 출력, `Ledger`/`TranscriptIngestWorker`/brain/server/GC 소유권 실행, SSH/Docker/RAGFlow management, direct RAGFlow write/delete/disable.

## 미결정 항목

- 없음. Phase 2에서 설계 중 실제 코드 제약이 요구사항 변경을 요구하면 `grill-to-spec` Phase 1로 회귀한다.
