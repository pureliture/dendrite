# Oh-My-Pi (omp) Provider Capture

`dendrite`에서 oh-my-pi(omp) 세션을 capture 대상 provider로 다루는 방법과 안전
경계를 정리한다. `dendrite`는 Mac thin-client이며 session-memory build/promote, GC,
backend write 같은 server/brain 책임은 갖지 않는다(그건 `neurons`).

## omp가 다른 provider와 다른 점

omp는 세션을 jsonl 파일 1개씩 저장한다. 다른 jsonl provider(codex/claude/gemini/
antigravity/grok)와 형태는 같지만, 파일 안에 메타데이터 entry(`title`,
`session`, `model_change`, `thinking_level_change`, `custom`,
`custom_message`, `title_change`, `model_usage`, `ttsr_injection`)와
비대화 role(`toolResult`, `developer`)이 섞여 있어 전체 opaque read가
적합하지 않다. 그래서 omp 전용 adapter가 user/assistant text만 분리한다.

- 기본 위치: `~/.omp/agent/sessions/<slug>/<timestamp>_<ULID>.jsonl`
- override: `PI_CODING_AGENT_DIR` 환경변수 → 그 아래 `sessions/` 디렉터리
- 대화 추출: `type == "message"` && `role in {"user","assistant"}` && `content[].type == "text"`만
- 제외: `thinking`, `toolCall`, `toolResult`, `developer`, `custom`, `custom_message`

## 통합 방식: source adapter 인터페이스 1개

- `JsonlSourceAdapter` (codex/claude/gemini/antigravity/grok): 파일을 텍스트로 읽어 redact.
- `OmpJsonlSourceAdapter` (omp): 같은 파일 읽지만 user/assistant text 블록만 조립 → redact.
- `HermesSqliteSourceAdapter` (hermes): profile별 `state.db`를 **read-only/immutable**로 읽기.
- `OpenCodeSqliteSourceAdapter` (opencode): `opencode.db`를 **read-only/immutable**로 열어
  해당 세션의 메시지만 조회 → redact.

세 adapter 모두 같은 `conversation_chunk`를 만들어 보내므로, omp도 다른 provider와
**동일한 형태로 neurons에 전송**되고 neurons는 그대로 수용한다(별도 수정 불필요).

- capture는 다른 provider처럼 **locator-only**다(파일 경로만 기록, 본문은 capture 시점에
  읽지 않음). 본문 읽기는 drain(thin shipper) 시점에 adapter가 한다.
- adapter는 파일을 **read-only**로만 읽는다. symlink 거부, 존재하지 않으면
  `source_unreadable`/`source_unproven`으로 quarantine한다.
- `session_id`가 비어 있는 경우 또는 locator가 없으면 quarantine한다(세션 간 유출 차단).
- 긴 세션은 drain이 여러 `conversation_chunk`로 자동 분할한다(버디지트 180K자).
- **fail-closed**: 등록되지 않은 provider는 `adapter_for()`에서
  `ValueError("source_unproven")`를 내어 drain이 quarantine한다. 조용한 jsonl
  fallback은 더 이상 존재하지 않는다.

## Enable 방법

omp는 `~/.omp/agent/extensions/*.ts` 아래 TypeScript extension으로 이벤트를
받을 수 있다. 다른 provider와 동일하게 hook 자동설치는 deferred(operator 수동
연결), capture는 hook 또는 explicit invocation으로 동작한다.

### 1) Extension 설치 (operator 수동)

`~/.omp/agent/extensions/dendrite-capture.ts` (in-process extension,
`pi.on(event, handler)`):

```typescript
// omp extension (개념형)
pi.on("agent_end", async (event) => {
  if (!event.isTerminal) return
  const payload = JSON.stringify({
    hook_event_name: "agent_end",
    session_id: ctx.sessionId,
    transcript_path: ctx.sessionFile,
  })
  // spawn capture CLI (stdin)
  // uv run python -m dendrite transcript-capture --provider omp --project <project> --spool ... --stdin-json --non-fatal
})
```

현재 contract가 `source_locator_unverified`/`deferred_not_installed` 상태이므로
`provider hook-plan`은 `blocked_source_unproven`을 반환한다. 자동 설치는
수행하지 않는다.

### 2) capture (locator-only spool)

```bash
echo '{"hook_event_name":"agent_end","session_id":"<ULID>","transcript_path":"'${HOME}'/.omp/agent/sessions/-Projects/2026-11-08T00-26-37-718Z_<ULID>.jsonl"}' \
  | uv run python -m dendrite transcript-capture \
      --provider omp \
      --project your-project \
      --spool "$HOME/.local/state/dendrite/capture-spool" \
      --stdin-json
```

store 경로를 payload에 주지 않으면 `PI_CODING_AGENT_DIR` → `~/.omp/agent/sessions/` 순으로
해석한다(존재할 때만). locator 키 우선순위:
`omp_state_db_path`/`state_db_path`/`session_db_path`/`transcript_path`/`source_locator` →
`PI_CODING_AGENT_DIR` → 기본 경로.

### 3) drain (ship)

```bash
uv run python -m dendrite transcript-drain --once \
  --capture-spool "$HOME/.local/state/dendrite/capture-spool" \
  --ingress-url "http://<approved-ingress-host>:18080"
# omp 항목은 session jsonl을 읽어 conversation_chunk로 전송된다.
```

### 4) 벌크 마이그레이션 (과거 세션 백필)

```bash
uv run python -m dendrite transcript-migrate --spool "$HOME/.local/state/dendrite/capture-spool" \
  --provider omp --dry-run
```

- `--source-root omp=/path/to/sessions` 로 override 가능.
- report는 카운트만(원경로·세션id·내용 미출력).

### 5) hook plan (non-mutating, deferred)

```bash
uv run python -m dendrite provider hook-plan --provider omp --action install
```

source contract가 아직 runtime-verified로 승격되지 않았으므로 `planned_status`는
`blocked_source_unproven`이며 어떤 config도 자동 변경하지 않는다(`mutation_performed: false`).

## 안전 경계 (반드시 지킨다)

- omp 세션 파일은 **read-only**로만 읽는다. write/delete 금지.
- raw transcript body, 원 파일 경로, raw session_id, token/cookie/bearer,
  dataset_id/document_id를 stdout/report/public metadata로 출력하지 않는다.
- shipper는 approved ingress endpoint(`POST /v1/ingest/enqueue`)만 사용한다.
- session-memory build/promote, GC, backend write는 dendrite가 하지 않는다(neurons 소유).

## 미검증 사항

- omp 세션 포맷은 omp 18.16.0 실측 기준이며, adapter는 알 수 없는 블록을 버리고
  아는 텍스트만 추출하는 tolerant 구조다. 실제 omp 설치 대상 live-smoke 후
  contract를 `source_locator_verified`로 승격할 수 있다.
- hook 이벤트 후보는 `agent_end{isTerminal:true}` / `session_shutdown`이며,
  `session.idle`은 존재하지 않는다(실측).
