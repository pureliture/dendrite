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

이 저장소는 M1 bootstrap 상태다. 기능 코드는 아직
`capabilities/agent-knowledge`에서 실제 thin seam 추출 전이다.
