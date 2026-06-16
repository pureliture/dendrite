from __future__ import annotations

import json
from pathlib import Path

import pytest

from dendrite.transcript_migrate import (
    build_migration_request,
    default_source_roots,
    enumerate_sessions,
    migrate,
    parse_source_root_overrides,
)


def _make_session(root: Path, rel: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # content is irrelevant: migration is locator-only and never reads it
    path.write_text('{"type":"user"}\n', encoding="utf-8")
    return path


def _roots(tmp_path: Path) -> dict[str, Path]:
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    _make_session(codex, "2026/06/sess-a.jsonl")
    _make_session(codex, "2026/06/sess-b.jsonl")
    _make_session(claude, "projects/neurons/sess-c.jsonl")
    _make_session(claude, "notes.txt")  # ignored (not jsonl)
    return {"codex": codex, "claude": claude}


# --- enumeration --------------------------------------------------------------


def test_enumerate_finds_jsonl_only(tmp_path):
    roots = _roots(tmp_path)
    codex_files = enumerate_sessions(roots["codex"])
    assert len(codex_files) == 2
    assert all(p.suffix == ".jsonl" for p in codex_files)
    claude_files = enumerate_sessions(roots["claude"])
    assert len(claude_files) == 1  # notes.txt excluded


def test_enumerate_missing_root_is_empty(tmp_path):
    assert enumerate_sessions(tmp_path / "nope") == []


def test_enumerate_skips_symlinks(tmp_path):
    root = tmp_path / "codex"
    real = _make_session(root, "real.jsonl")
    link = root / "link.jsonl"
    link.symlink_to(real)
    assert enumerate_sessions(root) == [real]


# --- migrate ------------------------------------------------------------------


def test_dry_run_counts_but_spools_nothing(tmp_path):
    roots = _roots(tmp_path)
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots=roots, providers=["codex", "claude"], dry_run=True)
    assert report["dry_run"] is True
    assert report["spooled"] == 3
    assert report["by_provider"]["codex"]["found"] == 2
    assert not spool.exists() or not list((spool / "pending").glob("*.json"))


def test_migrate_spools_locator_only_requests(tmp_path):
    roots = _roots(tmp_path)
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots=roots, providers=["codex", "claude"])
    assert report["spooled"] == 3
    assert report["errors"] == 0

    pending = sorted((spool / "pending").glob("*.json"))
    assert len(pending) == 3
    for f in pending:
        req = json.loads(f.read_text(encoding="utf-8"))
        assert req["content_policy"] == "locator_only"
        assert req["source_locator"]["runtime_handle"].endswith(".jsonl")
        assert req["source_locator"]["raw_path_present"] is True
        # no raw transcript content fields leaked into the request
        for forbidden in ("conversation", "messages", "transcript", "turns"):
            assert forbidden not in req


def test_provider_filter(tmp_path):
    roots = _roots(tmp_path)
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots=roots, providers=["codex"])
    assert report["spooled"] == 2
    assert "claude" not in report["by_provider"]


def test_limit_caps_per_provider(tmp_path):
    roots = _roots(tmp_path)
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots=roots, providers=["codex"], limit=1)
    assert report["by_provider"]["codex"]["spooled"] == 1


def test_root_unavailable_reported(tmp_path):
    spool = tmp_path / "spool"
    report = migrate(
        spool_root=spool, roots={"codex": tmp_path / "missing"}, providers=["codex"], dry_run=True
    )
    assert report["by_provider"]["codex"]["status"] == "root_unavailable"
    assert report["spooled"] == 0


def test_unsupported_provider_reported(tmp_path):
    report = migrate(spool_root=tmp_path / "s", roots={}, providers=["openai"], dry_run=True)
    assert report["by_provider"]["openai"]["status"] == "unsupported_provider"


# --- request builder + overrides ---------------------------------------------


def test_build_migration_request_is_valid_locator_only(tmp_path):
    path = _make_session(tmp_path / "codex", "s.jsonl")
    req = build_migration_request("codex", path, project="neurons")
    assert req["provider"] == "codex"
    assert req["content_policy"] == "locator_only"
    assert req["source_locator"]["runtime_handle"] == str(path)
    assert req["source_locator"]["locator_hash"].startswith("sha256:")


def test_parse_source_root_overrides(tmp_path):
    roots = parse_source_root_overrides([f"gemini={tmp_path}/g", f"antigravity={tmp_path}/a"])
    assert roots["gemini"] == Path(f"{tmp_path}/g")
    assert roots["antigravity"] == Path(f"{tmp_path}/a")
    # untouched providers keep defaults
    assert roots["codex"] == default_source_roots()["codex"]


def test_parse_source_root_overrides_rejects_bad(tmp_path):
    with pytest.raises(ValueError):
        parse_source_root_overrides(["nopath"])
    with pytest.raises(ValueError):
        parse_source_root_overrides(["unknownprovider=/x"])
