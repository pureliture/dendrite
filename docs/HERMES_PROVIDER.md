# Hermes Provider Capture

`dendrite`에서 Hermes agent 세션을 capture 대상 provider로 다루는 방법과 안전
경계를 정리한다. `dendrite`는 Mac thin-client이며, server brain / session-memory
build / GC / RAGFlow write는 책임지지 않는다.

## Hermes가 다른 provider와 다른 점

Hermes Agent(Nous Research)는 모든 세션을 **단일 SQLite store**에 저장한다.

- 기본 위치: `~/.hermes/state.db` (override: 환경변수 `HERMES_HOME` → `$HERMES_HOME/state.db`)
- 동반 파일: `state.db-wal`, `state.db-shm`

codex/claude/gemini/antigravity는 per-session `*.jsonl` 파일을 쓰지만 Hermes는 하나의
DB만 쓴다. 그래서 jsonl glob 기반 `transcript-migrate`와 transcript body를 텍스트로
읽어 ship하는 경로는 Hermes에 적용되지 않는다.

## 통합 방식: locator pointer provider

dendrite는 Hermes를 **locator-only pointer provider**로 통합한다.

- store의 **경로(locator)와 안전 metadata만** 기록한다. SQLite를 open / connect /
  query 하지 않는다.
- locator 존재 확인은 파일 존재만 본다. 없으면 경로를 날조하지 않고 빈 locator로 둔다.
- drain은 SQLite body를 읽지 않고 **locator pointer 문서**(provider, project, locator
  hash, version hash, observed_at)를 approved ingress(`POST /v1/ingest/enqueue`)로 보낸다.
- 실제 세션 본문 추출(SQLite 파싱)은 `neurons`(server/brain)의 책임이다.

## Enable 방법

Hermes hook/extension API는 1차 자료로 확정되지 않았다. 따라서 다른 provider와
동일하게 hook 자동 설치는 deferred이며, capture는 explicit invocation으로 동작한다.

### 1) capture (locator-only spool)

```bash
# 명시 store 경로
echo '{"hook_event_name":"Stop","session_id":"<id>","transcript_path":"'$HOME'/.hermes/state.db","workspacePaths":["'$HOME'/Projects/your-project"]}' \
  | uv run python -m dendrite transcript-capture \
      --provider hermes \
      --project your-project \
      --spool "$HOME/.local/state/dendrite/capture-spool" \
      --stdin-json
```

store 경로를 payload에 주지 않으면 dendrite가 `HERMES_HOME` → `~/.hermes/state.db`
순으로 해석한다(존재할 때만).

payload locator 키 우선순위: `hermes_db_path` / `state_db_path` / `session_db_path`
또는 공통 키 `transcript_path` / `source_locator` → `HERMES_HOME` → 기본 경로.

### 2) ship (thin shipper)

```bash
uv run python -m dendrite transcript-drain --once \
  --capture-spool "$HOME/.local/state/dendrite/capture-spool" \
  --ingress-url "http://<approved-ingress-host>:18080"
```

### 3) hook plan (non-mutating, deferred)

```bash
uv run python -m dendrite provider hook-plan --provider hermes --action install
```

Hermes는 live-smoke되지 않았으므로 `planned_status`는 `blocked_source_unproven`이며
어떤 config도 자동 변경하지 않는다(`mutation_performed: false`).

## 안전 경계 (반드시 지킨다)

- SQLite store를 open / read / query 하지 않는다. WAL checkpoint를 유발하지 않는다.
- Hermes DB에 write / delete 하지 않는다.
- raw transcript body, 원 store 경로, token/cookie/bearer, dataset_id/document_id를
  stdout / report / public metadata로 출력하지 않는다(원 경로는 private spool에만 남는다).
- shipper는 approved ingress endpoint(`POST /v1/ingest/enqueue`)만 사용한다. URL에
  credential을 넣지 않는다.
- server brain / session-memory build / GC / RAGFlow write는 dendrite가 하지 않는다.

## 샘플 설정

provider hook을 직접 설치할 때 사용할 수 있는 argv 형태(예시, 실제 설치는 operator
승인 후 수동):

```json
{
  "type": "command",
  "command": "dendrite transcript-capture --provider hermes --project <project> --spool <private-capture-spool> --stdin-json --non-fatal"
}
```

환경변수:

```bash
# Hermes store 위치 override (기본 ~/.hermes/state.db)
export HERMES_HOME="$HOME/.hermes"
```
