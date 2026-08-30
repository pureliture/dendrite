# Antigravity project_id 우선 project resolution Milestones

## 문서 상태

- 상태: approved
- 승인 상태: 사용자 승인 완료 (2026-08-30)
- Requirements: [requirements.md](requirements.md)
- Design: [design.md](design.md)
- 실행 계약: `agentic-execution` (requirements/design 승인 후에만 구현)
- Source: [pureliture/dendrite Issue #9](https://github.com/pureliture/dendrite/issues/9)
- 작성일: 2026-08-30

## 확인된 시작 근거

- Issue #9는 `conversation_summaries.project_id`가 223/223 세션에 존재하고 `workspace_uris`는 71/223 세션에만 존재한다고 보고한다.
- Issue #9는 `project_id`를 `~/.gemini/config/projects/<id>.json`의 이름 또는 경로로 역해결하는 방향을 제안한다.
- 현재 구현 seam은 `src/dendrite/transcript_capture.py`의 `_resolve_project()`와 Antigravity conversation summary resolver다.
- PR #8의 payload workspace 우선, `workspace_uris` 역참조, opaque session fallback, locator-only boundary가 현재 기준이다.
- 실제 project definition 구조 확인에서 top-level `name`, `projectResources.resources`, `gitFolder.folderUri` key가 확인됐고 raw 값은 출력하지 않았다.

## Slice 상태

| Slice | 상태 | Observable result |
| --- | --- | --- |
| S1. project_id metadata resolution | completed | usable payload workspace가 없을 때 project_id 기반 project definition으로 project label을 만든다. |
| S2. fallback and compatibility | completed | project_id miss, workspace_uris fallback, session fallback, TUI/launch-dir 및 privacy 경계가 유지된다. |
| S3. 전체 검증 및 closeout | in_progress | focused tests와 전체 `uv run pytest -q` 결과로 Issue #9 수용 기준을 확인한다. |

## Active slice

- S1 evidence: `uv run pytest -q tests/test_antigravity_capture_payload.py` → `32 passed`.
- S1 결과: project definition의 bounded candidate 우선순위와 resources가 비어 있는 경우의 top-level name fallback이 통과했다.
- S2 evidence: `uv run pytest -q tests/test_antigravity_capture_payload.py tests/test_agy_headless_capture.py` → `37 passed`.
- S2 결과: project_id miss/workspace_uris fallback, session fallback, payload workspace precedence, symlink guard와 launch-dir compatibility가 통과했다.
- 현재 active slice: S3. 전체 검증 및 closeout
- 구현 시작 조건: requirements, design, milestones 문서의 사용자 승인 완료
- 구현 방식: 승인된 한 observable slice만 active로 두고 결과 evidence를 확인한 뒤 다음 slice로 이동한다.

## Amendments and pending decisions

- 현재 승인된 amendment: requirements와 privacy boundary를 보존하기 위해 project definition resource 후보 뒤에 top-level `name` fallback을 추가했다. 실제 구조 확인과 focused `32 passed`가 근거다.
- 현재 pending decision: 없음
- `agy` 호출 convention 표준화, global Stop hook 설치/활성화, `neurons` server/runtime 변경은 이 issue의 pending implementation decision이 아니라 비목표다.

## S3 evidence

- 전체 regression: `uv run pytest -q` → `173 passed`.
- live metadata smoke: raw 값을 출력하지 않는 immutable read-only 집계에서 223개 summary row, project_id 223개, project definition hit 144개, project_id miss 후 workspace_uris hit 22개, session fallback 57개를 확인했다.
- 실제 `agy --print` global Stop hook smoke는 merge 후 `main` 코드에서 수행한다.

## Next action

변경 diff를 검토하고 commit·push·PR delivery를 진행한 뒤, merge 후 승인된 global Stop hook의 실제 `agy --print` smoke와 cleanup을 수행한다.

이 milestones 문서는 2026-08-30 사용자 승인으로 approved 상태이며, slice 진행 상태와 evidence의 source of truth다.
