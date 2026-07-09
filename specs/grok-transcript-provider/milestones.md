# Milestones — grok-transcript-provider

Goal: approved design.md (Grok Build provider on dendrite)

## M1 Allowlists + contract + hook-plan/doctor
- status: done
- mode: normal
- delegation: single-executor + mid-flight architecture review + code-simplifier
- tdd: failing tests first then green
- evidence: `tests/test_grok_capture_payload.py` + `test_provider_contracts` include grok; `uv run pytest -q` green

## M2 Capture normalize + locator resolve
- status: done
- evidence: Stop/camelCase payload, transcriptPath, GROK_HOME session resolve, session_id_hash + public_summary privacy asserts

## M3 Migrate enumerate updates.jsonl only
- status: done
- evidence: enumerate/migrate tests ignore chat_history.jsonl; same normalize schema

## M4 Full pytest + mid reviews
- status: done
- evidence: `uv run pytest -q` → 157 passed; architecture review (must-fix applied); code-simplifier clarity edits retested
