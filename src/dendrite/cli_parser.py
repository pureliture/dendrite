"""CLI argument parsing."""
from __future__ import annotations

from argparse import ArgumentParser

from .providers.registry import supported_names
from .transcript_migrate import MIGRATION_PROVIDERS


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="dendrite",
        description="Mac thin-client for provider capture, local spool, and neurons ingress enqueue.",
    )
    parser.add_argument("--version", action="store_true", help="print the dendrite version and exit")
    parser.add_argument(
        "--show-boundary",
        action="store_true",
        help="print the current client responsibility boundary and exit",
    )
    subparsers = parser.add_subparsers(dest="command")

    capture_fixture = subparsers.add_parser("capture-fixture", help="spool a minimized event from a JSON fixture")
    capture_fixture.add_argument("--fixture", required=True)
    capture_fixture.add_argument("--spool", required=True)

    capture_event_stdin = subparsers.add_parser("capture", help="spool a minimized event from stdin JSON")
    capture_event_stdin.add_argument("--provider", required=True)
    capture_event_stdin.add_argument("--project", required=True)
    capture_event_stdin.add_argument("--spool", required=True)
    capture_event_stdin.add_argument("--stdin-json", action="store_true")
    capture_event_stdin.add_argument("--non-fatal", action="store_true")

    provider = subparsers.add_parser("provider", help="provider hook planning utilities")
    provider_subparsers = provider.add_subparsers(dest="provider_command")
    provider_subparsers.add_parser("doctor", help="print provider contract readiness")
    hook_plan = provider_subparsers.add_parser("hook-plan", help="print a non-mutating provider hook plan")
    hook_plan.add_argument("--provider", required=True, choices=list(supported_names()))
    hook_plan.add_argument("--action", required=True, choices=["install", "uninstall"])
    hook_plan.add_argument("--project", default="<project>")
    hook_plan.add_argument("--capture-spool", default="<private-transcript-capture-spool>")

    capture = subparsers.add_parser("transcript-capture", help="enqueue a provider locator-only capture request")
    capture.add_argument("--provider", required=True, choices=list(supported_names()))
    capture.add_argument("--project", required=True)
    capture.add_argument("--spool", required=True)
    capture.add_argument("--stdin-json", action="store_true", help="read provider hook payload JSON from stdin")
    capture.add_argument("--non-fatal", action="store_true", help="return success after reporting capture errors")
    capture.add_argument("--kickstart-label", help="best-effort launchctl kickstart label after spooling")
    capture.add_argument("--hermes-profile", default="", help="Hermes profile name for profile-scoped capture")
    capture.add_argument("--require-workspace-path", action="store_true", help="skip capture when provider payload has no usable workspace path")

    drain = subparsers.add_parser("transcript-drain", help="drain locator-only transcript capture spool to ingress")
    drain.add_argument("--once", action="store_true", help="run one bounded drain tick")
    drain.add_argument("--capture-spool", required=True)
    drain.add_argument("--ingress-url", required=True)
    drain.add_argument("--target-profile", default="index-transcript-memory")
    drain.add_argument("--max-items", type=int, default=5, help="maximum capture requests and ingress POST attempts per tick")
    drain.add_argument("--timeout-seconds", type=float, default=10.0)
    drain.add_argument("--requeue-recoverable-quarantine", action="store_true")
    drain.add_argument("--runtime-dir", default="")
    drain.add_argument("--scheduler-id", default="")
    drain.add_argument("--scheduler-command-kind", default="")

    migrate_cmd = subparsers.add_parser(
        "transcript-migrate",
        help="bulk-spool locator-only capture requests for all historical provider sessions",
    )
    migrate_cmd.add_argument("--spool", required=True)
    migrate_cmd.add_argument("--project", default="", help="fallback project label; neurons re-resolves authority")
    migrate_cmd.add_argument("--provider", action="append", choices=list(MIGRATION_PROVIDERS), help="limit to provider(s); repeatable")
    migrate_cmd.add_argument("--source-root", action="append", help="override a provider source root as provider=/path; repeatable")
    migrate_cmd.add_argument("--hermes-profile", action="append", help="limit Hermes migration to profile(s); repeatable")
    migrate_cmd.add_argument("--limit", type=int, help="max sessions per provider (smoke runs)")
    migrate_cmd.add_argument("--dry-run", action="store_true", help="enumerate and count without spooling")

    source_catalog = subparsers.add_parser("source-catalog", help="local SourceRef catalog utilities")
    source_subparsers = source_catalog.add_subparsers(dest="source_catalog_command")
    source_scan = source_subparsers.add_parser("scan", help="write public SourceRef metadata and a private local index")
    source_scan.add_argument("--root", required=True)
    source_scan.add_argument("--root-id", required=True)
    source_scan.add_argument("--device-id", required=True)
    source_scan.add_argument("--public-out", required=True)
    source_scan.add_argument("--private-index", required=True)
    source_scan.add_argument("--sync-policy", default="metadata_only")
    source_scan.add_argument("--permission-scope", default="project")
    source_scan.add_argument("--limit", type=int)
    source_resolve = source_subparsers.add_parser("resolve", help="resolve a SourceRef from the private same-device index")
    source_resolve.add_argument("--private-index", required=True)
    source_resolve.add_argument("--source-ref-id", required=True)
    source_resolve.add_argument("--requesting-device-id", required=True)
    source_resolve.add_argument("--approval-ref", default="")
    source_resolve.add_argument("--expected-content-hash", default="")
    source_resolve.add_argument("--max-bytes", type=int, default=4096)

    return parser
