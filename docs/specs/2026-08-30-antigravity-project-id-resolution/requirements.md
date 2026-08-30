# Antigravity project_id 우선 project resolution Requirements

## 문서 상태

- 상태: approved
- 승인 상태: 사용자 승인 완료 (2026-08-30)
- Source: [pureliture/dendrite Issue #9](https://github.com/pureliture/dendrite/issues/9)
- Related: [Issue #7](https://github.com/pureliture/dendrite/issues/7), [PR #8](https://github.com/pureliture/dendrite/pull/8)
- Repo: `dendrite`
- 작성일: 2026-08-30

## 문제

PR #8의 Antigravity headless project resolution은 `conversation_summaries.workspace_uris`에 의존한다.

Issue #9의 2026-08-30 실측에서는 local summary store 223개 세션 중 `workspace_uris`가 채워진 세션은 71개(32%)이고 `project_id`가 채워진 세션은 223개(100%)였다.

`project_id`는 `~/.gemini/config/projects/<id>.json`에서 프로젝트 이름 또는 경로로 역해결할 수 있으므로, `workspace_uris`가 비어 있는 headless 세션의 `unknown-<sha8>(session)` fallback을 줄일 수 있다.

## 목표

Antigravity capture가 직접 전달된 workspace가 없는 경우 `project_id`를 첫 번째 metadata project label source로 사용하고, `workspace_uris`와 opaque session fallback을 순서대로 유지한다.

직접 workspace를 포함하는 TUI/interactive payload와 launch-dir capture는 기존 project label 동작을 유지한다.

## 기능 요구사항

### FR-0. Metadata project resolution precedence

Antigravity의 metadata resolution 순서는 `project_id` → `workspace_uris` → `unknown-<sha8>(session)`이어야 한다.

payload에 usable workspace path가 있으면 그 path를 기존처럼 가장 먼저 사용하여 TUI/interactive 세션의 정확도를 보존해야 한다.

`project_id`와 `workspace_uris`가 모두 usable하지 않으면 Issue #7과 PR #8에서 정한 opaque session fallback을 사용해야 한다.

### FR-1. `project_id` 역해결

`provider=antigravity` capture는 payload에 usable workspace가 없을 때 `conversationId`로 local conversation summary metadata를 read-only로 조회할 수 있어야 한다.

summary row의 `project_id`를 `~/.gemini/config/projects/<id>.json`의 project definition과 연결해야 한다.

project definition에서는 다음 후보를 순서대로 검사해야 한다.

- `projectResources.resources[0].gitFolder.folderUri`
- `projectResources.resources[0].folderUri`
- `projectResources.resources[0].name`

후보 path 또는 `file://` URI는 기존 workspace source path guard와 URL decoding 및 `canonicalize_project()`를 거쳐 project label로 사용해야 한다.

project definition이 없거나 읽을 수 없거나 usable candidate가 없으면 capture를 실패시키지 않고 다음 metadata source인 `workspace_uris`로 진행해야 한다.

### FR-2. 기존 `workspace_uris` 호환

`project_id`가 miss이고 `workspace_uris`에 usable 값이 있으면 PR #8과 동일한 project canonicalization 결과를 반환해야 한다.

`project_id`가 hit하더라도 usable project definition을 만들지 못하면 `workspace_uris`를 계속 시도해야 한다.

### FR-3. Opaque session fallback

`project_id`와 `workspace_uris` 모두 miss이고 `conversationId`가 있으면 raw session id를 노출하지 않는 `unknown-<sha8>(session)` 형태의 안정적인 session fallback을 사용해야 한다.

`conversationId` 자체가 없으면 기존 capture contract의 `--project` fallback behavior를 유지해야 한다.

### FR-4. Compatibility

workspace를 포함하는 TUI/interactive Stop payload는 기존 workspace 기준 project label을 계속 사용해야 한다.

`agy-headless-capture`의 launch-dir label 경로는 유지하고 metadata 역해결과 상호 호환되어야 한다.

`codex`, `claude`, `gemini`, `hermes`, `grok`의 project resolution behavior는 변경하지 않아야 한다.

### FR-5. Privacy와 read-only safety

conversation summary store와 project definition은 read-only 방식으로만 열어야 하며 SQLite write 또는 WAL checkpoint를 일으키지 않아야 한다.

raw `project_id`, raw `conversationId`, private path, transcript body, token, cookie, bearer, API key 또는 secret-like value를 public output, log, report, shipped payload에 출력하지 않아야 한다.

metadata가 없거나 읽을 수 없는 경우 private path와 내부 오류를 출력하지 않고 다음 fallback으로 종료해야 한다.

### FR-6. Test coverage

관련 테스트는 다음 observable behavior를 검증해야 한다.

- `project_id` hit이 named project 또는 usable project path를 project label로 만든다.
- `project_id` miss + `workspace_uris` hit이 PR #8과 동일하게 동작한다.
- `project_id`와 `workspace_uris`가 모두 miss이면 `unknown-<sha8>(session)`이 기록된다.
- usable payload workspace가 metadata보다 우선한다.
- TUI/interactive resolution과 `agy-headless-capture` launch-dir label이 회귀하지 않는다.
- project definition과 summary store가 read-only 경계를 지키고 raw 값이 public output에 누출되지 않는다.
- non-Antigravity provider behavior와 기존 locator-only capture boundary가 유지된다.

## 비목표

- Antigravity core 또는 `agy` CLI 자체 수정
- headless 실행을 repo 안에서 `--project <name>`으로 표준화하는 호출 convention 변경
- provider config, global Stop hook, LaunchAgent의 자동 mutation 또는 설치/활성화
- `neurons` server-side ingest, ledger, session-memory build/promote, RAGFlow projection, GC 또는 운영 배포 변경
- raw transcript를 capture hook에서 읽거나 저장하는 기능
- Antigravity local metadata schema migration 또는 write-back
- metadata가 항상 존재한다고 가정하는 동작

## 수용 기준

- headless `agy --print` Stop payload에서 `project_id`가 역해결되면 실제 project name/path가 `project`로 기록된다.
- `project_id` 역해결이 불가능하고 `workspace_uris`가 usable하면 PR #8과 동일한 label이 기록된다.
- 두 metadata source가 모두 miss이면 `${PWD}`와 구별되는 opaque session fallback이 기록된다.
- TUI/interactive 및 launch-dir project label 정확도에 regression이 없다.
- 관련 Antigravity tests와 전체 client boundary tests가 통과한다.
- provider hook → locator-only spool/outbox → thin shipper → `POST 18080` 경계와 privacy contract가 유지된다.

이 requirements 문서는 2026-08-30 사용자 승인으로 approved 상태이며, 이 문서가 구현 요구사항의 source of truth다.
