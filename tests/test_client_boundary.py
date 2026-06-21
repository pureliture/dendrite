from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "dendrite"

FORBIDDEN_IMPORT_ROOTS = {
    "agent_knowledge",
}

FORBIDDEN_SOURCE_FRAGMENTS = {
    "Docker",
    "GC scheduler",
    "Ledger",
    "MemoryCard",
    "RAGFLOW_API_KEY",
    "RagflowHttpClient",
    "StateDBIngressSink",
    "ToolEvidenceSyncRunner",
    "TranscriptIngestWorker",
    "brain_query",
    "direct RAGFlow delete",
    "direct RAGFlow disable",
    "direct RAGFlow write",
    "docker",
    "ragflow_client",
    "server_runtime",
    "session_memory_gc",
    "ssh ragflow-ubuntu",
    "transcript_memory_gc",
}


def _source_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_dendrite_source_does_not_import_source_monolith() -> None:
    violations: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}: from {node.module} import ...")

    assert violations == []


def test_dendrite_source_does_not_contain_server_brain_authority_symbols() -> None:
    violations: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for fragment in FORBIDDEN_SOURCE_FRAGMENTS:
            if fragment in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)} contains {fragment}")

    assert violations == []
