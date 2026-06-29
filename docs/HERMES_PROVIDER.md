# Hermes Provider Capture

`dendrite`에서 Hermes agent 세션을 capture 대상 provider로 다루는 방법과 안전
경계를 정리한다. `dendrite`는 Mac thin-client이며 session-memory build/promote, GC,
RAGFlow write 같은 server/brain 책임은 갖지 않는다(그건 `neurons`).

## Hermes가 다른 provider와 다른 점

Hermes Agent(Nous Research)는 모든 세션을 **단일 SQLite store**에 저장한다.

- 기본 위치: `~/.hermes/state.db` (override: 환경변수 `HERMES_HOME` → `$HERMES_HOME/state.db`)
- 동반 파일: `state.db-wal`, `state.db-shm`
- 테이블: `sessions`, `messages` 등

codex/claude/gemini/antigravity는 세션마다 `*.jsonl` 파일을 쓰지만 Hermes는 DB 하나에
모든 세션을 담는다.

## 통합 방식: source adapter 인터페이스 1개

dendrite는 provider 소스를 읽어 redacted transcript 문서(`conversation_chunk`)로
보내는 일을 **하나의 source adapter 인터페이스**로 처리한다.

- `JsonlSourceAdapter` (codex/claude/gemini/antigravity): `.jsonl` 파일을 텍스트로 읽어 redact.
- `HermesSqliteSourceAdapter` (hermes): `state.db`를 **read-only/immutable**로 열어 해당
  세션의 메시지만 조회 → redact.

두 adapter 모두 같은 `conversation_chunk`를 만들어 보내므로, Hermes도 다른 provider와
**동일한 형태로 neurons에 전송**되고 neurons는 그대로 수용한다(별도 수정 불필요).

- capture는 다른 provider처럼 **locator-only**다(store 경로만 기록, 본문은 capture 시점에
  읽지 않음). 본문 읽기는 drain(thin shipper) 시점에 adapter가 한다.
- Hermes adapter는 store를 **read-only/immutable**로만 연다: 쓰기 없음, WAL checkpoint
  유발 없음. 읽기 불가/스키마 불일치는 quarantine(크래시 아님).

## Enable 방법

Hermes는 hook이 있다(공식 docs): shell hook을 `~/.hermes/config.yaml`에 등록해 세션종료
이벤트(`on_session_end`)에 명령을 실행할 수 있고, stdin JSON에 `session_id`/`cwd`를 준다
(transcript/DB 경로는 주지 않으므로 dendrite가 store를 해석). 다른 provider와 동일하게
hook 자동설치는 deferred(operator 수동 연결), capture는 hook 또는 explicit invocation으로 동작.

### 1) capture (locator-only spool)

```bash
echo '{"hook_event_name":"on_session_end","session_id":"<id>","transcript_path":"'$HOME'/.hermes/state.db","workspacePaths":["'$HOME'/Projects/your-project"]}' \
  | uv run python -m dendrite transcript-capture \
      --provider hermes \
      --project your-project \
      --spool "$HOME/.local/state/dendrite/capture-spool" \
      --stdin-json
```

store 경로를 payload에 주지 않으면 dendrite가 `HERMES_HOME` → `~/.hermes/state.db`
순으로 해석한다(존재할 때만). locator 키 우선순위: `hermes_db_path`/`state_db_path`/
`session_db_path`/`transcript_path`/`source_locator` → `HERMES_HOME` → 기본 경로.

### 2) drain (ship)

```bash
uv run python -m dendrite transcript-drain --once \
  --capture-spool "$HOME/.local/state/dendrite/capture-spool" \
  --ingress-url "http://<approved-ingress-host>:18080"
# hermes 항목은 state.db를 read-only로 읽어 conversation_chunk로 전송된다.
```

### 2c) 벌크 마이그레이션 (과거 세션 백필)

기존에 쌓인 Hermes 세션을 일괄로 보낼 때는 `transcript-migrate`를 쓴다. jsonl provider와
달리 Hermes는 `state.db`의 세션을 read-only로 열거해 세션별 locator-only request를 spool하고,
이후 `transcript-drain`이 세션별 `conversation_chunk`로 ship한다.

```bash
# 1) 먼저 안전하게 세션 수만 확인 (read-only, 아무것도 안 보냄)
uv run python -m dendrite transcript-migrate --spool "$HOME/.local/state/dendrite/capture-spool" \
  --provider hermes --dry-run
# -> by_provider.hermes.found = 세션 수

# 2) 일부만 스모크 (--limit), 또는 전체 spool
uv run python -m dendrite transcript-migrate --spool "$HOME/.local/state/dendrite/capture-spool" \
  --provider hermes --limit 1

# 3) drain으로 세션별 conversation_chunk ship
uv run python -m dendrite transcript-drain --once \
  --capture-spool "$HOME/.local/state/dendrite/capture-spool" \
  --ingress-url "http://<approved-ingress-host>:18080"
```

- store 경로 override: `--source-root hermes=/path/to/state.db` (없으면 `HERMES_HOME` → 기본).
- **멱등**: 같은 세션·같은 store 상태면 re-run해도 중복 적재되지 않는다.
- 열거는 read-only/immutable(쓰기·checkpoint 없음), report는 카운트만(원경로/세션id/내용 미출력).

### 3) hook plan (non-mutating, deferred)

```bash
uv run python -m dendrite provider hook-plan --provider hermes --action install
```

Hermes는 live-smoke되지 않았으므로 `planned_status`는 `blocked_source_unproven`이며 어떤
config도 자동 변경하지 않는다(`mutation_performed: false`).

## 안전 경계 (반드시 지킨다)

- Hermes DB는 **read-only/immutable**로만 연다. write/delete 금지, WAL checkpoint 유발 금지.
- raw transcript body, 원 store 경로, raw session_id, token/cookie/bearer, dataset_id/
  document_id를 stdout/report/public metadata로 출력하지 않는다(원경로·raw session_id는
  private spool에만 남는다).
- shipper는 approved ingress endpoint(`POST /v1/ingest/enqueue`)만 사용한다. URL에
  credential을 넣지 않는다.
- session-memory build/promote, GC, RAGFlow write는 dendrite가 하지 않는다(neurons 소유).

## 샘플 설정

provider hook을 직접 설치할 때 사용할 수 있는 argv 형태(예시; 실제 설치는 operator 승인 후
수동, `~/.hermes/config.yaml`의 shell hook):

```yaml
hooks:
  on_session_end:
    - matcher: ""
      command: "dendrite transcript-capture --provider hermes --project <project> --spool <private-capture-spool> --stdin-json --non-fatal"
      timeout: 5
```

환경변수:

```bash
# Hermes store 위치 override (기본 ~/.hermes/state.db)
export HERMES_HOME="$HOME/.hermes"
```

## 미검증 사항

- Hermes SQLite 스키마(테이블/컬럼명)는 공식 session-storage 문서 기준 가정이며, adapter는
  컬럼 존재에 tolerant하게 구현됐다. 실제 Hermes 설치 대상 live-smoke 후 contract를
  `source_locator_verified`로 승격할 수 있다.
