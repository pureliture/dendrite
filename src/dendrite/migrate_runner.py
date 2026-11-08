"""Bulk migration execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .capture_spool import TranscriptCaptureSpool
from .providers.registry import supported_names
from .source_adapters.hermes import enumerate_hermes_sessions
from .source_adapters.opencode import enumerate_opencode_sessions
from .hermes_profiles import HermesProfileStore, discover_hermes_profile_stores, slug_hermes_profile
from .migrate_requests import (
    build_grok_migration_request,
    build_hermes_migration_request,
    build_migration_request,
    build_opencode_migration_request,
)
from .migrate_roots import default_source_roots

SESSION_GLOB = "**/*.jsonl"
GROK_UPDATES_NAME = "updates.jsonl"
MIGRATION_PROVIDERS = tuple(supported_names())
SUPPORTED_TRANSCRIPT_PROVIDERS = frozenset(supported_names())


def enumerate_sessions(root: Path, *, pattern: str = SESSION_GLOB) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob(pattern) if p.is_file() and not p.is_symlink())


def enumerate_grok_sessions(root: Path) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob(GROK_UPDATES_NAME)
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
    roots = roots if roots is not None else default_source_roots()
    providers = providers or list(MIGRATION_PROVIDERS)
    spool = None if dry_run else TranscriptCaptureSpool(spool_root)
    report = MigrationReport(dry_run=dry_run)

    for provider in providers:
        if provider not in SUPPORTED_TRANSCRIPT_PROVIDERS:
            report.by_provider[provider] = {"status": "unsupported_provider", "found": 0, "spooled": 0, "errors": 0}
            continue
        if provider == "hermes":
            summary = _migrate_hermes(roots.get("hermes"), spool, project=project, profiles=hermes_profiles, limit=limit, dry_run=dry_run, report=report)
        elif provider == "grok":
            summary = _migrate_grok(roots.get("grok"), spool, project=project, limit=limit, dry_run=dry_run, report=report)
        elif provider == "opencode":
            summary = _migrate_opencode(roots.get("opencode"), spool, project=project, limit=limit, dry_run=dry_run, report=report)
        else:
            summary = _migrate_jsonl(provider, roots.get(provider), spool, project=project, limit=limit, dry_run=dry_run, report=report)
        report.by_provider[provider] = summary
        report.spooled += summary["spooled"]
        report.errors += summary["errors"]

    return report.as_dict()


def _migrate_jsonl(provider: str, root: Path | None, spool: TranscriptCaptureSpool | None, *, project: str, limit: int | None, dry_run: bool, report: MigrationReport) -> dict:
    if not root or not Path(root).is_dir():
        return {"status": "root_unavailable", "root": str(root or ""), "found": 0, "spooled": 0, "errors": 0}
    files = _limit_items(enumerate_sessions(Path(root)), limit)
    spooled = errors = 0
    for path in files:
        try:
            request = build_migration_request(provider, path, project=project)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "root": str(root), "found": len(files), "spooled": spooled, "errors": errors}


def _migrate_grok(root: Path | None, spool: TranscriptCaptureSpool | None, *, project: str, limit: int | None, dry_run: bool, report: MigrationReport) -> dict:
    if not root or not Path(root).is_dir():
        return {"status": "root_unavailable", "found": 0, "spooled": 0, "errors": 0}
    files = _limit_items(enumerate_grok_sessions(Path(root)), limit)
    spooled = errors = 0
    for path in files:
        try:
            request = build_grok_migration_request(path, project=project)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "found": len(files), "spooled": spooled, "errors": errors}


def _migrate_opencode(root: Path | None, spool: TranscriptCaptureSpool | None, *, project: str, limit: int | None, dry_run: bool, report: MigrationReport) -> dict:
    db_path = _resolve_opencode_store(root)
    if db_path is None:
        return {"status": "root_unavailable", "found": 0, "spooled": 0, "errors": 0}
    if not dry_run and limit is None:
        found = len(enumerate_opencode_sessions(db_path))
        return {"status": "bounded_limit_required", "found": found, "spooled": 0, "errors": 0}
    sessions = _limit_items(enumerate_opencode_sessions(db_path), limit)
    spooled = errors = 0
    for session_id in sessions:
        try:
            request = build_opencode_migration_request(db_path, session_id, project=project)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "found": len(sessions), "spooled": spooled, "errors": errors}


def _resolve_opencode_store(root: Path | None) -> Path | None:
    if root is None:
        return None
    candidate = Path(root)
    if candidate.is_file() and not candidate.is_symlink():
        return candidate
    if candidate.is_dir() and not candidate.is_symlink():
        db_path = candidate / "opencode.db"
        if db_path.is_file() and not db_path.is_symlink():
            return db_path
    return None


def _migrate_hermes(root: Path | None, spool: TranscriptCaptureSpool | None, *, project: str, profiles: list[str] | None, limit: int | None, dry_run: bool, report: MigrationReport) -> dict:
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
    total_found = total_spooled = total_errors = 0
    profile_summaries: dict[str, dict] = {}
    for store in stores:
        summary = _migrate_hermes_store(store, spool, project=project, limit=limit, dry_run=dry_run, report=report)
        profile_summaries[store.profile] = summary
        total_found += summary["found"]
        total_spooled += summary["spooled"]
        total_errors += summary["errors"]
    return {"status": "ok", "found": total_found, "spooled": total_spooled, "errors": total_errors, "profiles": profile_summaries}


def _blocked_unbounded_hermes_summary(stores: list[HermesProfileStore]) -> dict:
    profile_summaries = {}
    total_found = 0
    for store in stores:
        found = len(enumerate_hermes_sessions(store.path))
        total_found += found
        profile_summaries[store.profile] = {"status": "bounded_limit_required", "found": found, "spooled": 0, "errors": 0}
    return {"status": "bounded_limit_required", "found": total_found, "spooled": 0, "errors": 0, "profiles": profile_summaries}


def _migrate_hermes_store(store: HermesProfileStore, spool: TranscriptCaptureSpool | None, *, project: str, limit: int | None, dry_run: bool, report: MigrationReport) -> dict:
    sessions = _limit_items(enumerate_hermes_sessions(store.path), limit)
    spooled = errors = 0
    for session_id in sessions:
        try:
            request = build_hermes_migration_request(store.path, session_id, project=project, profile=store.profile)
            if not dry_run:
                spool.enqueue(request)
            spooled += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            report.record_error_class(exc)
    return {"status": "ok", "found": len(sessions), "spooled": spooled, "errors": errors}


def _limit_items(items: list, limit: int | None) -> list:
    if limit is None:
        return items
    return items[: max(limit, 0)]


def parse_source_root_overrides(values: list[str] | None) -> dict[str, Path]:
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
