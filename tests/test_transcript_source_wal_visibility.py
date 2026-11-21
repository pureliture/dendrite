from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

WRITER = textwrap.dedent(
    """
    import sqlite3, sys, time
    con = sqlite3.connect(sys.argv[1])
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp TEXT)")
    con.execute("INSERT INTO messages VALUES ('s-wal', 'user', 'wal-only-message', '2026-01-01T00:00:00Z')")
    con.commit()
    print("ready", flush=True)
    time.sleep(30)
    """
)

LOCK_WRITER = textwrap.dedent(
    """
    import sqlite3, sys, time
    con = sqlite3.connect(sys.argv[1])
    con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp TEXT)")
    con.execute("INSERT INTO messages VALUES ('s-wal', 'user', 'committed', '2026-01-01T00:00:00Z')")
    con.commit()
    con.execute("BEGIN EXCLUSIVE")
    con.execute("INSERT INTO messages VALUES ('s-wal', 'user', 'uncommitted', '2026-01-01T00:00:01Z')")
    print("locked", flush=True)
    time.sleep(10)
    """
)

READER = textwrap.dedent(
    """
    import sys
    from dendrite.source_adapters.hermes import read_hermes_session_text
    print(read_hermes_session_text(sys.argv[1], 's-wal'))
    """
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    return env


def _start(script: str, db: Path, marker: str) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(db)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == marker
    return proc


def _read(db: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", READER, str(db)],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=15,
    )


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def test_wal_only_commit_survives_sigkill(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    writer = _start(WRITER, db, "ready")
    try:
        writer.kill()
        writer.wait(timeout=5)
        assert Path(str(db) + "-wal").is_file()
        immutable = sqlite3.connect(f"file:{db.resolve()}?mode=ro&immutable=1", uri=True)
        try:
            rows = immutable.execute("SELECT name FROM sqlite_master WHERE name = 'messages'").fetchall()
        finally:
            immutable.close()
        assert rows == []
        result = _read(db)
        assert result.returncode == 0, result.stderr
        assert "wal-only-message" in result.stdout
    finally:
        _stop(writer)


def test_live_writer_wal_commit_is_visible(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    writer = _start(WRITER, db, "ready")
    try:
        result = _read(db)
        assert result.returncode == 0, result.stderr
        assert "wal-only-message" in result.stdout
    finally:
        _stop(writer)


def test_missing_store_fails_closed(tmp_path: Path) -> None:
    result = _read(tmp_path / "missing.db")
    assert result.returncode != 0
    assert "source_unreadable" in result.stderr


def test_corrupt_store_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    db.write_text("not a sqlite database\n", encoding="utf-8")
    result = _read(db)
    assert result.returncode != 0
    assert "source_unreadable" in result.stderr


def test_locked_store_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    writer = _start(LOCK_WRITER, db, "locked")
    try:
        result = _read(db)
        assert result.returncode != 0
        assert "source_unreadable" in result.stderr
    finally:
        _stop(writer)
