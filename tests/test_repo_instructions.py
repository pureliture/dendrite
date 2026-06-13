from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_provider_instruction_files_exist_and_preserve_thin_client_boundary() -> None:
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "한국어" in text
        assert "provider hook -> locator-only" in text
        assert "POST 18080" in text
        assert "RAGFlow direct write" in text or "direct RAGFlow write" in text
        assert "neurons" in text


def test_agents_forbids_server_brain_authority() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for forbidden_owner in (
        "`Ledger` ownership",
        "`TranscriptIngestWorker` ownership",
        "session-memory build/promote/read SoT",
        "brain.query, MemoryCard, native memory",
        "GC live execute",
        "`ssh ragflow-ubuntu`",
    ):
        assert forbidden_owner in text
