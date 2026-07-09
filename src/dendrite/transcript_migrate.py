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

from .hermes_profiles import HermesProfileStore, discover_hermes_profile_stores, slug_hermes_profile
from .transcript_capture import (
    SUPPORTED_TRANSCRIPT_PROVIDERS,
    TranscriptCaptureSpool,
    normalize_provider_capture_request,
)
from .transcript_source import enumerate_hermes_sessions

MIGRATION_PROVIDERS = ("codex", "claude", "gemini", "antigravity", "hermes", "grok")
SESSION_GLOB = "**/*.jsonl"
GROK_UPDATES_NAME = "updates.jsonl"


def default_source_roots() -> dict[str, Path]:
    """Best-effort per-provider session-store roots (override as needed).

    Note: codex/claude/gemini/antigravity roots are directories of jsonl session
    files; the hermes root is a Hermes home directory (~/.hermes) or a single
    SQLite store file for compatibility; the grok root is ``$GROK_HOME/sessions``.
    """
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    hermes_home = os.environ.get("HERMES_HOME")
    hermes_root = Path(hermes_home) if hermes_home else (home / ".hermes")
    grok_home = Path(os.environ.get("GROK_HOME") or (home / ".grok"))
    return {
        "codex": codex_home / "sessions",
        "claude": home / ".claude" / "projects",
        "gemini": home / ".gemini",
        "antigravity": home / ".antigravity",
        "hermes": hermes_root,
        "grok": grok_home / "sessions",
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


def build_hermes_migration_request(
    db_path: Path, session_id: str, *, project: str = "", profile: str = "default"
) -> dict:
    """Build a locator-only capture request for one historical Hermes session.

    The locator is the SQLite store path; the (private) session id selects which
    session the drain's SQLite adapter extracts. Body is never read here.
    """
    payload = {"transcript_path": str(db_path), "session_id": session_id, "hermes_profile": profile}
    return normalize_provider_capture_request("hermes", payload, project=project)


def build_grok_migration_request(path: Path, *, project: str = "") -> dict:
    """Build a locator-only capture request for one historical Grok session file.

    Session id is the parent directory name (``…/<session-id>/updates.jsonl``).
    """
    path = Path(path)
    session_id = path.parent.name
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(path),
        "session_id": session_id,
        "sessionId": session_id,
    }
    return normalize_provider_capture_request("grok", payload, project=project)


def enumerate_grok_sessions(root: Path) -> list[Path]:
    """Return each session's ``updates.jsonl`` under ``root`` (no other jsonl)."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob(GROK_UPDATES_NAME)
        if path.is_file() and not path.is_symlink()
    )


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

    def record_error_class(self, exc: Exception) -> None:
        name = exc.__class__.__name__
        self.error_classes[name] = self.error_classes.get(name, 0) + 1


def migrate(
    *,
    spool_root: str | Path,
    roots: dict[str, Path] | None = None,
    project: str = "",
    providers: list[str] | None = None,
    hermes_profiles: list[str] | None = None,
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
        if provider == "hermes":
            summary = _migrate_hermes(
                roots.get("hermes"),
                spool,
                project=project,
                profiles=hermes_profiles,
                limit=limit,
                dry_run=dry_run,
                report=report,
            )
        elif provider == "grok":
            summary = _migrate_grok(
                roots.get("grok"), spool, project=project, limit=limit, dry_run=dry_run, report=report
            )
        else:
            summary = _migrate_jsonl(
                provider, roots.get(provider), spool, project=project, limit=limit, dry_run=dry_run, report=report
            )
        report.by_provider[provider] = summary
        report.spooled += summary["spooled"]
        report.errors += summary["errors"]

    return report.as_dict()


def _migrate_jsonl(
    provider: str,
    root: Path | None,
    spool: TranscriptCaptureSpool | None,
    *,
    project: str,
    limit: int | None,
    dry_run: bool,
    report: MigrationReport,
) -> dict:
    """Migrate a directory of per-session jsonl files (codex/claude/gemini/antigravity)."""
    if not root or not Path(root).is_dir():
        return {"status": "root_unavailable", "root": str(root or ""), "found": 0, "spooled": 0, "errors": 0}
    files = _limit_items(enumerate_sessions(Path(root)), limit)
    spooled = 0
    errors = 0
    for path in files:
        try:
            request = build_migration_request(provider, path, project=project)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001 - per-file fail-soft; count + continue
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "root": str(root), "found": len(files), "spooled": spooled, "errors": errors}


def _migrate_grok(
    root: Path | None,
    spool: TranscriptCaptureSpool | None,
    *,
    project: str,
    limit: int | None,
    dry_run: bool,
    report: MigrationReport,
) -> dict:
    """Migrate Grok sessions: one ``updates.jsonl`` per session directory.

    Report carries counts only — never the source root path or session ids
    (same privacy posture as Hermes migrate).
    """
    if not root or not Path(root).is_dir():
        return {"status": "root_unavailable", "found": 0, "spooled": 0, "errors": 0}
    files = _limit_items(enumerate_grok_sessions(Path(root)), limit)
    spooled = 0
    errors = 0
    for path in files:
        try:
            request = build_grok_migration_request(path, project=project)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001 - per-file fail-soft; count + continue
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "found": len(files), "spooled": spooled, "errors": errors}


def _migrate_hermes(
    root: Path | None,
    spool: TranscriptCaptureSpool | None,
    *,
    project: str,
    profiles: list[str] | None,
    limit: int | None,
    dry_run: bool,
    report: MigrationReport,
) -> dict:
    """Migrate discovered Hermes profile stores by enumerating sessions read-only.

    The report carries counts only — never the raw store path or session ids.
    """
    if not root:
        return {"status": "root_unavailable", "found": 0, "spooled": 0, "errors": 0}
    stores = discover_hermes_profile_stores(root)
    if profiles:
        requested = {slug_hermes_profile(profile) for profile in profiles}
        stores = [store for store in stores if store.profile in requested]
    if not stores:
        return {"status": "root_unavailable", "found": 0, "spooled": 0, "errors": 0}
    if not dry_run and limit is None:
        return _blocked_unbounded_hermes_summary(stores)
    total_found = 0
    total_spooled = 0
    total_errors = 0
    profile_summaries: dict[str, dict] = {}
    for store in stores:
        summary = _migrate_hermes_store(
            store,
            spool,
            project=project,
            limit=limit,
            dry_run=dry_run,
            report=report,
        )
        profile_summaries[store.profile] = summary
        total_found += summary["found"]
        total_spooled += summary["spooled"]
        total_errors += summary["errors"]
    return {
        "status": "ok",
        "found": total_found,
        "spooled": total_spooled,
        "errors": total_errors,
        "profiles": profile_summaries,
    }


def _blocked_unbounded_hermes_summary(stores: list[HermesProfileStore]) -> dict:
    profile_summaries = {}
    total_found = 0
    for store in stores:
        found = len(enumerate_hermes_sessions(store.path))
        total_found += found
        profile_summaries[store.profile] = {
            "status": "bounded_limit_required",
            "found": found,
            "spooled": 0,
            "errors": 0,
        }
    return {
        "status": "bounded_limit_required",
        "found": total_found,
        "spooled": 0,
        "errors": 0,
        "profiles": profile_summaries,
    }


def _migrate_hermes_store(
    store: HermesProfileStore,
    spool: TranscriptCaptureSpool | None,
    *,
    project: str,
    limit: int | None,
    dry_run: bool,
    report: MigrationReport,
) -> dict:
    sessions = _limit_items(enumerate_hermes_sessions(store.path), limit)
    spooled = 0
    errors = 0
    for session_id in sessions:
        try:
            request = build_hermes_migration_request(store.path, session_id, project=project, profile=store.profile)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001 - per-session fail-soft; count + continue
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "found": len(sessions), "spooled": spooled, "errors": errors}


def _limit_items(items: list, limit: int | None) -> list:
    if limit is None:
        return items
    return items[: max(limit, 0)]


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
    "build_grok_migration_request",
    "build_hermes_migration_request",
    "build_migration_request",
    "default_source_roots",
    "discover_hermes_profile_stores",
    "enumerate_grok_sessions",
    "enumerate_sessions",
    "migrate",
    "parse_source_root_overrides",
]
