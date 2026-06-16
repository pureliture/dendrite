"""Bulk historical transcript migration: enumerate provider sessions -> spool.

This is the client-side (dendrite) half of the CouchDB transcript-source
migration. It walks each provider's on-disk session store and spools a
**locator-only** capture request per session file into the same
``TranscriptCaptureSpool`` that ``transcript-drain`` already ships to the neurons
ingress. It never reads transcript *content*: only the file path (locator) is
recorded, exactly like the live ``transcript-capture`` path. neurons reads the
locator and parses/rebuilds server-side into the CouchDB source store.

Provider source roots are configurable. codex/claude have confident defaults;
gemini/antigravity layouts vary by install, so override them with
``--source-root provider=/path`` when the default does not match.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .transcript_capture import (
    SUPPORTED_TRANSCRIPT_PROVIDERS,
    TranscriptCaptureSpool,
    normalize_provider_capture_request,
)

MIGRATION_PROVIDERS = ("codex", "claude", "gemini", "antigravity")
SESSION_GLOB = "**/*.jsonl"


def default_source_roots() -> dict[str, Path]:
    """Best-effort per-provider session-store roots (override as needed)."""
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    return {
        "codex": codex_home / "sessions",
        "claude": home / ".claude" / "projects",
        "gemini": home / ".gemini",
        "antigravity": home / ".antigravity",
    }


def enumerate_sessions(root: Path, *, pattern: str = SESSION_GLOB) -> list[Path]:
    """Return the session files under ``root`` (no symlinks, files only, sorted)."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob(pattern) if p.is_file() and not p.is_symlink())


def build_migration_request(provider: str, path: Path, *, project: str = "") -> dict:
    """Build a locator-only capture request for one historical session file.

    Only the path is passed as the transcript locator; neurons re-derives the
    canonical session identity from the file content when it parses server-side.
    """
    payload = {"transcript_path": str(path)}
    return normalize_provider_capture_request(provider, payload, project=project)


@dataclass
class MigrationReport:
    dry_run: bool = False
    spooled: int = 0
    errors: int = 0
    by_provider: dict = field(default_factory=dict)
    error_classes: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "schema_version": "dendrite_transcript_migrate_result.v1",
            "status": "ok",
            "dry_run": self.dry_run,
            "spooled": self.spooled,
            "errors": self.errors,
            "by_provider": self.by_provider,
            "error_classes": self.error_classes,
        }


def migrate(
    *,
    spool_root: str | Path,
    roots: dict[str, Path] | None = None,
    project: str = "",
    providers: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Enumerate sessions per provider and spool locator-only capture requests."""
    roots = roots if roots is not None else default_source_roots()
    providers = providers or list(MIGRATION_PROVIDERS)
    spool = None if dry_run else TranscriptCaptureSpool(spool_root)
    report = MigrationReport(dry_run=dry_run)

    for provider in providers:
        if provider not in SUPPORTED_TRANSCRIPT_PROVIDERS:
            report.by_provider[provider] = {"status": "unsupported_provider", "found": 0, "spooled": 0, "errors": 0}
            continue
        root = roots.get(provider)
        if not root or not Path(root).is_dir():
            report.by_provider[provider] = {
                "status": "root_unavailable",
                "root": str(root or ""),
                "found": 0,
                "spooled": 0,
                "errors": 0,
            }
            continue

        files = enumerate_sessions(Path(root))
        if limit is not None:
            files = files[: max(limit, 0)]
        prov_spooled = 0
        prov_errors = 0
        for path in files:
            try:
                request = build_migration_request(provider, path, project=project)
                if not dry_run:
                    spool.enqueue(request)
                prov_spooled += 1
            except Exception as exc:  # noqa: BLE001 - per-file fail-soft; count + continue
                prov_errors += 1
                name = exc.__class__.__name__
                report.error_classes[name] = report.error_classes.get(name, 0) + 1

        report.by_provider[provider] = {
            "status": "ok",
            "root": str(root),
            "found": len(files),
            "spooled": prov_spooled,
            "errors": prov_errors,
        }
        report.spooled += prov_spooled
        report.errors += prov_errors

    return report.as_dict()


def parse_source_root_overrides(values: list[str] | None) -> dict[str, Path]:
    """Parse ``--source-root provider=/path`` overrides onto the defaults."""
    roots = default_source_roots()
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"--source-root must be provider=path, got: {raw}")
        provider, _, path = raw.partition("=")
        provider = provider.strip()
        if provider not in MIGRATION_PROVIDERS:
            raise ValueError(f"unknown provider in --source-root: {provider}")
        roots[provider] = Path(path.strip()).expanduser()
    return roots


__all__ = [
    "MIGRATION_PROVIDERS",
    "MigrationReport",
    "build_migration_request",
    "default_source_roots",
    "enumerate_sessions",
    "migrate",
    "parse_source_root_overrides",
]
