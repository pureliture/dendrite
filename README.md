# dendrite

`dendrite`는 LLM-brain/neurons로 transcript locator와 redacted enqueue payload를
전달하는 Mac thin-client 저장소다.

현재 책임:

- provider hook
- locator-only capture
- local spool/outbox
- thin shipper
- `POST 18080` enqueue client

명시적으로 책임이 아닌 것:

- `Ledger`
- `TranscriptIngestWorker`
- direct RAGFlow writer
- session-memory build/promote
- brain.query / MemoryCard / native memory
- GC safety execution

## Development

```text
uv run pytest -q
uv run python -m dendrite --help
```

## Current Status

이 저장소는 Mac thin-client seam을 이미 로컬로 보유한다. 현재 command
surface는 다음과 같다.

- `dendrite capture-fixture`: JSON fixture를 minimized local event로 spool
- `dendrite capture`: stdin JSON을 minimized local event로 spool
- `dendrite transcript-capture`: provider hook payload를 locator-only capture
  request로 spool
- `dendrite provider doctor`: provider source contract readiness 확인
- `dendrite provider hook-plan`: non-mutating provider hook plan 출력

`transcript_ingest.py`는 thin enqueue body/client seam만 담는다. server worker,
ledger/state authority, direct RAGFlow writer, session-memory build/promote, brain
query, native memory, GC safety는 `neurons` 책임이다.

`tests/test_client_boundary.py` guards this boundary by rejecting imports from
the historical `agent_knowledge` source monolith and server/brain authority
symbols in `src/dendrite`.
