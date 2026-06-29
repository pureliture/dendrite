from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dendrite.transcript_migrate import (
    build_migration_request,
    default_source_roots,
    enumerate_sessions,
    migrate,
    parse_source_root_overrides,
)
from dendrite.transcript_source import enumerate_hermes_sessions


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


# --- hermes (SQLite store) migration -----------------------------------------


def _make_hermes_db(path: Path, sessions: dict[str, list[tuple[str, str]]]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, role TEXT, content TEXT, timestamp INTEGER)"
        )
        ts = 0
        for sid, msgs in sessions.items():
            for role, content in msgs:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    (sid, role, content, ts),
                )
                ts += 1
        conn.commit()
    finally:
        conn.close()


def _make_hermes_db_no_session_col(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
        conn.execute("INSERT INTO messages (role, content) VALUES ('user', 'hi')")
        conn.commit()
    finally:
        conn.close()


def test_enumerate_hermes_sessions_distinct_sorted(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"s-b": [("user", "x")], "s-a": [("user", "y"), ("assistant", "z")]})
    assert enumerate_hermes_sessions(db) == ["s-a", "s-b"]


def test_enumerate_hermes_single_session_when_no_session_col(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db_no_session_col(db)
    assert enumerate_hermes_sessions(db) == [""]


def test_enumerate_hermes_empty_for_non_sqlite(tmp_path):
    f = tmp_path / "state.db"
    f.write_text("not a sqlite database\n", encoding="utf-8")
    assert enumerate_hermes_sessions(f) == []


def test_enumerate_hermes_is_read_only(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"s": [("user", "x")]})
    before = db.stat()
    enumerate_hermes_sessions(db)
    after = db.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


def test_hermes_migrate_dry_run_counts_sessions(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"a": [("u", "1")], "b": [("u", "2")], "c": [("u", "3")]})
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots={"hermes": db}, providers=["hermes"], dry_run=True)
    assert report["by_provider"]["hermes"]["found"] == 3
    assert not spool.exists() or not list((spool / "pending").glob("*.json"))


def test_hermes_migrate_spools_per_session_locator_only(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"a": [("u", "1")], "b": [("u", "2")]})
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots={"hermes": db}, providers=["hermes"])
    assert report["by_provider"]["hermes"]["spooled"] == 2

    pending = sorted((spool / "pending").glob("*.json"))
    assert len(pending) == 2
    sids = set()
    for f in pending:
        req = json.loads(f.read_text(encoding="utf-8"))
        assert req["provider"] == "hermes"
        assert req["content_policy"] == "locator_only"
        assert req["source_locator"]["runtime_handle"] == str(db)
        sids.add(req["session_id"])
        assert str(db) not in json.dumps(req["public_summary"], sort_keys=True)
    assert sids == {"a", "b"}


def test_hermes_migrate_limit(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"a": [("u", "1")], "b": [("u", "2")], "c": [("u", "3")]})
    spool = tmp_path / "spool"
    report = migrate(spool_root=spool, roots={"hermes": db}, providers=["hermes"], limit=1)
    assert report["by_provider"]["hermes"]["spooled"] == 1
    assert len(list((spool / "pending").glob("*.json"))) == 1


def test_hermes_migrate_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"a": [("u", "1")], "b": [("u", "2")]})
    spool = tmp_path / "spool"
    migrate(spool_root=spool, roots={"hermes": db}, providers=["hermes"])
    migrate(spool_root=spool, roots={"hermes": db}, providers=["hermes"])
    # second run must not create duplicate spool files
    assert len(list((spool / "pending").glob("*.json"))) == 2


def test_hermes_migrate_report_is_path_and_session_id_free(tmp_path):
    # The migration report must carry counts only — never the raw store path or
    # session ids (guards against a future "add root for symmetry" edit).
    db = tmp_path / "state.db"
    _make_hermes_db(db, {"sess-aaa": [("user", "1")], "sess-bbb": [("user", "2")]})
    report = migrate(spool_root=tmp_path / "spool", roots={"hermes": db}, providers=["hermes"])
    hermes = report["by_provider"]["hermes"]
    assert "root" not in hermes
    blob = json.dumps(report)
    assert str(db) not in blob
    assert "sess-aaa" not in blob
    assert "sess-bbb" not in blob


def test_hermes_migrate_root_unavailable(tmp_path):
    report = migrate(
        spool_root=tmp_path / "s",
        roots={"hermes": tmp_path / "missing.db"},
        providers=["hermes"],
        dry_run=True,
    )
    assert report["by_provider"]["hermes"]["status"] == "root_unavailable"
    assert report["spooled"] == 0
