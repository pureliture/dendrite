# OpenCode Provider Capture

`dendrite`에서 OpenCode 세션을 capture 대상 provider로 다루는 방법과 안전
경계를 정리한다. `dendrite`는 Mac thin-client이며 session-memory build/promote, GC,
backend write 같은 server/brain 책임은 갖지 않는다(그건 `neurons`).

## OpenCode가 다른 provider와 다른 점

OpenCode는 세션을 단일 SQLite store에 저장한다.

- 기본 위치: `~/.local/share/opencode/opencode.db`
- override: `OPENCODE_DB_PATH` (파일 직접 지정) 또는 `OPENCODE_HOME` → `$OPENCODE_HOME/opencode.db`
- 테이블: `session_v2` (세션 목록), `session_message` (`session_id`/`type`/`seq`/`data` JSON)
- 메시지 `type`: `user` (`data.text`), `assistant` (`data.content` 배열의 text/tool 블록),
  `synthetic` (shell 결과 텍스트). `system`/`idle` 행은 도구 카탈로그·결과 마커라
  대화가 아니므로 제외한다.

codex/claude/gemini/antigravity는 세션마다 `*.jsonl` 파일을 쓰지만 OpenCode는 DB 하나에
모든 세션을 담는다. Hermes와 같은 SQLite 단일-store 패턴이라 기존 adapter 구조를
그대로 따른다.

## 통합 방식: source adapter 인터페이스 1개

- `JsonlSourceAdapter` (codex/claude/gemini/antigravity/grok): 파일을 텍스트로 읽어 redact.
- `HermesSqliteSourceAdapter` (hermes): profile별 `state.db`를 **read-only/immutable**로 읽기.
- `OpenCodeSqliteSourceAdapter` (opencode): `opencode.db`를 **read-only/immutable**로 열어
  해당 세션의 메시지만 조회 → redact.

세 adapter 모두 같은 `conversation_chunk`를 만들어 보내므로, OpenCode도 다른 provider와
**동일한 형태로 neurons에 전송**되고 neurons는 그대로 수용한다(별도 수정 불필요).

- capture는 다른 provider처럼 **locator-only**다(store 경로만 기록, 본문은 capture 시점에
  읽지 않음). 본문 읽기는 drain(thin shipper) 시점에 adapter가 한다.
- adapter는 store를 **read-only/immutable**로만 연다: 쓰기 없음, WAL checkpoint
  유발 없음. 읽기 불가/스키마 불일치는 quarantine(크래시 아님).
- `session_id`가 비어 있으면 전체 덤프 대신 refuse(quarantine)한다. 세션 간
  내용 유출을 원천 차단한다.
- 긴 세션은 drain이 여러 `conversation_chunk`로 자동 분할한다(버디지트 180K자,
  `transcript_requires_multiple_parts`는 단일 문서 강제 호출에서만 발생).

## Enable 방법

OpenCode는 `~/.config/opencode/plugins/` 아래 TypeScript 플러그인으로
`session.idle` 이벤트를 받을 수 있다. 다른 provider와 동일하게
hook 자동설치는 deferred(operator 수동 연결), capture는 hook 또는 explicit invocation으로 동작.

### 1) 플러그인 설치 (operator 수동)

`~/.config/opencode/plugins/dendrite-capture.ts`:

```typescript
import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"

export const DendriteCapturePlugin: Plugin = async () => {
  return {
    event: async ({ event }) => {
      if (event.type !== "session.idle") return
      const sessionId = (event.properties as { sessionID?: string })?.sessionID
      if (!sessionId) return

      const payload = JSON.stringify({
        hook_event_name: "session.idle",
        sessionID: sessionId,
        timestamp: new Date().toISOString(),
      })

      const proc = spawn(
        "uv",
        ["run", "python", "-m", "dendrite", "transcript-capture",
         "--provider", "opencode", "--project", "<project>",
         "--spool", "<private-transcript-capture-spool>", "--stdin-json"],
        { stdio: ["pipe", "ignore", "ignore"], detached: true },
      )
      proc.stdin.write(payload)
      proc.stdin.end()
      proc.unref()
    },
  }
}

export default DendriteCapturePlugin
```

`<project>`와 `<private-transcript-capture-spool>`은 아래 plan 명령이
출력하는 정확한 argv로 치환한다.

### 2) capture (locator-only spool)

```bash
echo '{"hook_event_name":"session.idle","sessionID":"<id>","workspacePaths":["'$HOME'/Projects/your-project"]}' \
  | uv run python -m dendrite transcript-capture \
      --provider opencode \
      --project your-project \
      --spool "$HOME/.local/state/dendrite/capture-spool" \
      --stdin-json
```

store 경로를 payload에 주지 않으면 dendrite가 `OPENCODE_DB_PATH` →
`OPENCODE_HOME` → `~/.local/share/opencode/opencode.db` 순으로 해석한다(존재할 때만).
locator 키 우선순위:
`opencode_db_path`/`state_db_path`/`session_db_path`/`transcript_path`/`source_locator` →
`OPENCODE_DB_PATH` → `OPENCODE_HOME` → 기본 경로.

### 3) drain (ship)

```bash
uv run python -m dendrite transcript-drain --once \
  --capture-spool "$HOME/.local/state/dendrite/capture-spool" \
  --ingress-url "http://<approved-ingress-host>:18080"
# opencode 항목은 opencode.db를 read-only로 읽어 conversation_chunk로 전송된다.
```

### 4) 벌크 마이그레이션 (과거 세션 백필)

```bash
# 1) 먼저 안전하게 세션 수만 확인 (read-only, 아무것도 안 보냄)
uv run python -m dendrite transcript-migrate --spool "$HOME/.local/state/dendrite/capture-spool" \
  --provider opencode --dry-run
# -> by_provider.opencode.found = 세션 수

# 2) 일부만 스모크 (--limit)
uv run python -m dendrite transcript-migrate --spool "$HOME/.local/state/dendrite/capture-spool" \
  --provider opencode --limit 1

# 3) drain으로 세션별 conversation_chunk ship
uv run python -m dendrite transcript-drain --once \
  --capture-spool "$HOME/.local/state/dendrite/capture-spool" \
  --ingress-url "http://<approved-ingress-host>:18080"
```

- store 경로 override: `--source-root opencode=/path/to/opencode.db`
  (디렉터리를 주면 그 안의 `opencode.db`를 사용).
- OpenCode non-dry-run migration은 `--limit` 없는 unbounded spool을 거부한다.
- 열거는 read-only/immutable(쓰기·checkpoint 없음), report는 카운트만(원경로·세션id·내용 미출력).

### 5) hook plan (non-mutating, deferred)

```bash
uv run python -m dendrite provider hook-plan --provider opencode --action install
```

OpenCode는 source contract가 아직 runtime-verified로 승격되지 않았으므로 `planned_status`는
`blocked_source_unproven`이며 어떤 config도 자동 변경하지 않는다(`mutation_performed: false`).

## 안전 경계 (반드시 지킨다)

- OpenCode DB는 **read-only/immutable**로만 연다. write/delete 금지, WAL checkpoint 유발 금지.
- raw transcript body, 원 store 경로, raw session_id, token/cookie/bearer, dataset_id/
  document_id를 stdout/report/public metadata로 출력하지 않는다(원경로·raw session_id는
  private spool에만 남는다).
- shipper는 approved ingress endpoint(`POST /v1/ingest/enqueue`)만 사용한다. URL에
  credential을 넣지 않는다.
- session-memory build/promote, GC, backend write는 dendrite가 하지 않는다(neurons 소유).

## 미검증 사항

- `session_message.data` JSON 스키마(`content` 블록 변형)는 opencode v2.1.2 실측 기준이며,
  adapter는 알 수 없는 블록을 버리고 아는 텍스트만 추출하는 tolerant 구조다.
  실제 OpenCode 설치 대상 live-smoke 후 contract를 `source_locator_verified`로
  승격할 수 있다.
- `target_profile` 기본값은 neurons `TargetProfileRegistry` SSOT 기준 transcript 용도
  유효 id인 `index-transcript-memory`다(구 `ragflow-transcript-memory`·`transcript-memory`는
  모두 unknown으로 거부된다).
