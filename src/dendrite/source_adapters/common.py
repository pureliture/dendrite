"""Shared path validation."""
from __future__ import annotations

from pathlib import Path


def source_path(value) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("source_unproven")
    path = Path(value)
    if path.is_symlink():
        raise ValueError("source_policy_blocked")
    if not path.exists() or not path.is_file():
        raise ValueError("source_unreadable")
    return path
