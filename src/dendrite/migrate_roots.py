"""Provider source roots."""
from __future__ import annotations

import os
from pathlib import Path

def default_source_roots() -> dict[str, Path]:
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    hermes_home = os.environ.get("HERMES_HOME")
    hermes_root = Path(hermes_home) if hermes_home else (home / ".hermes")
    grok_home = Path(os.environ.get("GROK_HOME") or (home / ".grok"))
    opencode_home = os.environ.get("OPENCODE_HOME")
    opencode_db = Path(os.environ.get("OPENCODE_DB_PATH") or "") if os.environ.get("OPENCODE_DB_PATH") else None
    if opencode_db is None:
        opencode_db = (
            Path(opencode_home).expanduser() / "opencode.db"
            if opencode_home
            else home / ".local" / "share" / "opencode" / "opencode.db"
        )
    omp_sessions = Path(os.environ.get("PI_CODING_AGENT_DIR") or (home / ".omp" / "agent")) / "sessions"
    return {
        "codex": codex_home / "sessions",
        "claude": home / ".claude" / "projects",
        "gemini": home / ".gemini",
        "antigravity": home / ".antigravity",
        "hermes": hermes_root,
        "grok": grok_home / "sessions",
        "opencode": opencode_db,
        "omp": omp_sessions,
    }
