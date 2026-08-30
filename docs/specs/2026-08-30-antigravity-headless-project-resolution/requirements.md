# Antigravity Headless Project Resolution Requirements

## 문서 상태

- 상태: approved
- 승인 상태: 사용자 승인 완료 (2026-08-30)
- Source: [pureliture/dendrite Issue #7](https://github.com/pureliture/dendrite/issues/7)
- Repo: `dendrite`
- 작성일: 2026-08-30

## 문제

Antigravity `agy --print` 세션의 Stop hook payload는 `workspacePaths: []`를 보낼 수 있다.

현재 `src/dendrite/transcript_capture.py`의 `_resolve_project()`는 payload에서 사용할 수 있는 workspace/cwd를 찾지 못하면 hook 실행 시점에 전달된 `--project`를 fallback으로 사용한다.

Issue #7에서 관측한 결과, 이 fallback이 실제 세션이 실행된 프로젝트가 아니라 hook shell의 기본 cwd를 가리켜 `project`가 잘못 기록된다.

`agy-headless-capture`는 이미 실행 디렉터리를 `workspacePaths`로 전달하는 launch-dir capture 경로를 갖지만, global Stop hook의 headless payload에는 여전히 workspace 정보가 없을 수 있다.

## 확인된 사실

- Antigravity headless payload에는 `conversationId`, transcript locator, 빈 `workspacePaths`가 함께 올 수 있다.
- 기존 project label 처리는 usable workspace path를 먼저 찾고, 그 값을 `canonicalize_project()`로 정규화한다.
- provider storage path는 project label source로 사용하지 않도록 기존 필터가 존재한다.
- 현재 Antigravity local metadata에는 `conversation_summaries` SQLite store와 `conversation_id`, `workspace_uris` 필드가 존재한다.
- 현재 설치에서 확인된 summary row는 223개이고 그중 71개가 non-empty workspace metadata를 가진다. 이 확인은 값 자체를 출력하지 않는 read-only aggregate 검사로 수행했다.
- 저장소의 기존 경계는 provider hook → locator-only spool/outbox → thin shipper → `POST 18080`이며, server/brain/GC 책임은 `neurons`에 있다.

## 목표

headless `agy --print` Stop hook capture가 가능한 경우 실제 세션 workspace에서 project label을 결정하고, 불가능한 경우에도 기존 `${PWD}` fallback과 구별되는 중복 방지 식별자를 기록한다.

## 기능 요구사항

### FR-1. Headless conversation metadata 역참조

`provider=antigravity` capture는 payload에 usable workspace path가 없을 때 `conversationId`를 키로 provider의 local conversation metadata를 read-only로 조회할 수 있어야 한다.

조회 결과의 usable workspace URI/path는 기존 project source path 필터와 `canonicalize_project()`를 거쳐 project label로 사용해야 한다.

metadata 역참조는 Antigravity capture에만 적용하며 기존 `codex`, `claude`, `gemini`, `hermes`, `grok`의 project resolution behavior를 바꾸지 않아야 한다.

### FR-2. Resolution precedence

project label 결정 순서는 payload의 usable `workspacePaths` 또는 scalar workspace/cwd, conversation metadata의 workspace metadata, session fallback이어야 한다.

payload에 이미 유효한 workspace path가 있으면 metadata 역참조 결과가 이를 덮어쓰지 않아야 한다.

### FR-3. 중복 방지 fallback

`conversationId`가 있고 metadata 역참조가 불가능하거나 usable workspace를 제공하지 않으면 session 기반 opaque fallback을 사용해야 한다.

이 fallback은 Issue #7에서 제안한 `unknown-<sha8>(session)` 형태처럼 raw session id나 private path를 노출하지 않으면서 동일 session에 대해 안정적이고 서로 다른 session 간 중복을 줄여야 한다.

`conversationId` 자체가 없는 입력은 현재 capture contract의 fallback behavior를 유지해야 한다.

### FR-4. TUI 및 launch-dir compatibility

workspace를 포함하는 기존 TUI Stop payload는 현재 workspace 기준 project label을 계속 사용해야 한다.

`agy-headless-capture`의 launch-dir label 경로는 유지하고, 이번 변경의 metadata 역참조 경로와 상호 호환되어야 한다.

### FR-5. Privacy와 DB safety

metadata 조회는 read-only/immutable 방식으로만 수행하며 SQLite write, WAL checkpoint, provider 실행 mutation을 일으키지 않아야 한다.

raw transcript body, raw conversation/session id, private locator/path, token, cookie, bearer, API key 또는 secret-like value를 public output, report, shipped payload에 출력하지 않아야 한다.

metadata가 없거나 읽을 수 없는 경우 private path와 내부 오류 내용을 출력하지 않고 session fallback 또는 기존 fallback으로 종료해야 한다.

### FR-6. Test coverage

관련 테스트는 다음 observable behavior를 검증해야 한다.

- 빈 `workspacePaths`인 headless payload가 conversation metadata의 workspace로 project label을 결정한다.
- metadata에 match가 없거나 usable workspace가 없으면 session fallback이 기록된다.
- payload workspace가 있으면 metadata보다 payload workspace가 우선한다.
- TUI workspace resolution과 `agy-headless-capture` launch-dir label이 회귀하지 않는다.
- metadata store가 read-only/immutable로 열리고 raw 값이 public output에 누출되지 않는다.

## 비목표

- Antigravity core 또는 `agy` CLI 자체 수정
- provider config, global Stop hook, LaunchAgent의 자동 mutation
- `neurons` server-side ingest, ledger, session-memory build/promote, RAGFlow projection, GC 또는 운영 배포 변경
- raw transcript를 capture hook에서 읽거나 저장하는 기능
- Antigravity local metadata의 schema migration 또는 write-back
- headless 세션의 metadata가 항상 존재한다고 가정하는 동작

## 수용 기준

- headless `agy --print` Stop payload가 usable workspace metadata를 가지면 실제 실행 project가 `project`로 기록된다.
- TUI 세션의 기존 workspace 기준 동작에 regression이 없다.
- 역참조가 불가능한 session은 `${PWD}`와 구별되는 session 기반 opaque fallback으로 기록된다.
- 관련 `agy-headless-capture` 및 payload tests가 추가 또는 갱신된다.
- 기존 locator-only 및 `dendrite` client boundary가 유지된다.

이 requirements 문서는 2026-08-30 사용자 승인으로 approved 상태이며, 이 문서가 구현 요구사항의 source of truth다.
