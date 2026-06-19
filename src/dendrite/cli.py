from __future__ import annotations

from argparse import ArgumentParser
import json
import os
from pathlib import Path
import subprocess
import sys

from . import __version__
from .capture import capture_event
from .provider_contracts import build_provider_doctor_report, build_provider_hook_plan
from .source_catalog import resolve_source_ref, scan_source_catalog
from .transcript_capture import (
    TranscriptCaptureSpool,
    has_workspace_path,
    normalize_provider_capture_request,
)
from .transcript_drain import drain_transcript_spool_once
from .transcript_ingest import IngressQueueClient
from .transcript_migrate import MIGRATION_PROVIDERS, migrate, parse_source_root_overrides


def _best_effort_kickstart_launchagent(label: str) -> None:
    subprocess.run(
        ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=False,
    )


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
    hook_plan.add_argument("--provider", required=True, choices=["claude", "gemini", "codex", "antigravity"])
    hook_plan.add_argument("--action", required=True, choices=["install", "uninstall"])
    hook_plan.add_argument("--project", default="<project>")
    hook_plan.add_argument("--capture-spool", default="<private-transcript-capture-spool>")
    capture = subparsers.add_parser("transcript-capture", help="enqueue a provider locator-only capture request")
    capture.add_argument("--provider", required=True, choices=["claude", "gemini", "codex", "antigravity"])
    capture.add_argument("--project", required=True)
    capture.add_argument("--spool", required=True)
    capture.add_argument("--stdin-json", action="store_true", help="read provider hook payload JSON from stdin")
    capture.add_argument("--non-fatal", action="store_true", help="return success after reporting capture errors")
    capture.add_argument("--kickstart-label", help="best-effort launchctl kickstart label after spooling")
    capture.add_argument(
        "--require-workspace-path",
        action="store_true",
        help="skip capture when provider payload has no usable workspace path",
    )
    drain = subparsers.add_parser("transcript-drain", help="drain locator-only transcript capture spool to ingress")
    drain.add_argument("--once", action="store_true", help="run one bounded drain tick")
    drain.add_argument("--capture-spool", required=True)
    drain.add_argument("--ingress-url", required=True)
    drain.add_argument("--target-profile", default="ragflow-transcript-memory")
    drain.add_argument("--max-items", type=int, default=5)
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
    migrate_cmd.add_argument(
        "--provider", action="append", choices=list(MIGRATION_PROVIDERS), help="limit to provider(s); repeatable"
    )
    migrate_cmd.add_argument(
        "--source-root", action="append", help="override a provider source root as provider=/path; repeatable"
    )
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


def _print_capture_event_result(path) -> None:
    stored = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "schema_version": "dendrite_capture_result.v1",
                "status": "spooled",
                "event_id": stored["event_id"],
                "provider": stored["provider"],
                "project": stored["project"],
                "event_type": stored["event_type"],
                "content_hash": stored["content_hash"],
                "spool_file": str(path),
            },
            sort_keys=True,
        )
    )


def _capture_event_from_fixture(args) -> int:
    try:
        payload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("fixture payload must be a JSON object")
        path = capture_event(payload, spool_root=args.spool)
        _print_capture_event_result(path)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "capture_error", "error_class": exc.__class__.__name__}, sort_keys=True))
        return 1


def _capture_event_from_stdin(args) -> int:
    if not args.stdin_json:
        print(json.dumps({"status": "error", "error_class": "stdin_json_required"}, sort_keys=True))
        return 0 if args.non_fatal else 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("provider payload must be a JSON object")
        payload = {**payload, "provider": args.provider, "project": args.project}
        path = capture_event(payload, spool_root=args.spool)
        _print_capture_event_result(path)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "capture_error", "error_class": exc.__class__.__name__}, sort_keys=True))
        return 0 if args.non_fatal else 1


def _capture_from_stdin(args) -> int:
    if not args.stdin_json:
        print(json.dumps({"status": "error", "error_class": "stdin_json_required"}, sort_keys=True))
        return 0 if args.non_fatal else 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("provider payload must be a JSON object")
        if args.require_workspace_path and not has_workspace_path(payload):
            print(
                json.dumps(
                    {
                        "schema_version": "dendrite_transcript_capture_result.v1",
                        "status": "skipped_no_workspace_path",
                        "provider": args.provider,
                    },
                    sort_keys=True,
                )
            )
            return 0
        request = normalize_provider_capture_request(args.provider, payload, project=args.project)
        path = TranscriptCaptureSpool(args.spool).enqueue(request)
        if args.kickstart_label:
            _best_effort_kickstart_launchagent(args.kickstart_label)
        source_locator = request.get("source_locator") or {}
        print(
            json.dumps(
                {
                    "schema_version": "dendrite_transcript_capture_result.v1",
                    "status": "spooled",
                    "request_id": request["request_id"],
                    "provider": request["provider"],
                    "project": request["project"],
                    "event_type": request["event_type"],
                    "source_locator_hash": source_locator.get("locator_hash", ""),
                    "spool_file": str(path),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "dendrite_transcript_capture_result.v1",
                    "status": "capture_error",
                    "error_class": exc.__class__.__name__,
                },
                sort_keys=True,
            )
        )
        return 0 if args.non_fatal else 1


def _transcript_migrate(args) -> int:
    try:
        roots = parse_source_root_overrides(args.source_root)
        report = migrate(
            spool_root=args.spool,
            roots=roots,
            project=args.project,
            providers=args.provider,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "dendrite_transcript_migrate_result.v1",
                    "status": "migrate_error",
                    "error_class": exc.__class__.__name__,
                },
                sort_keys=True,
            )
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.show_boundary:
        print("provider hook -> locator-only spool -> thin shipper -> POST 18080")
        return 0

    if args.command == "capture-fixture":
        return _capture_event_from_fixture(args)

    if args.command == "capture":
        return _capture_event_from_stdin(args)

    if args.command == "provider":
        if args.provider_command == "doctor":
            print(json.dumps(build_provider_doctor_report(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.provider_command == "hook-plan":
            print(
                json.dumps(
                    build_provider_hook_plan(
                        provider=args.provider,
                        action=args.action,
                        project=args.project,
                        capture_spool=args.capture_spool,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

    if args.command == "transcript-capture":
        return _capture_from_stdin(args)

    if args.command == "transcript-migrate":
        return _transcript_migrate(args)

    if args.command == "source-catalog":
        if args.source_catalog_command == "scan":
            report = scan_source_catalog(
                root=args.root,
                root_id=args.root_id,
                device_id=args.device_id,
                public_out=args.public_out,
                private_index=args.private_index,
                sync_policy=args.sync_policy,
                permission_scope=args.permission_scope,
                limit=args.limit,
            )
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.source_catalog_command == "resolve":
            report = resolve_source_ref(
                private_index=args.private_index,
                source_ref_id=args.source_ref_id,
                requesting_device_id=args.requesting_device_id,
                approval_ref=args.approval_ref,
                expected_content_hash=args.expected_content_hash,
                max_bytes=args.max_bytes,
            )
            print(json.dumps(report, sort_keys=True))
            return 0

    if args.command == "transcript-drain":
        if not args.once:
            print(json.dumps({"status": "error", "error_class": "once_required"}, sort_keys=True))
            return 2
        report = drain_transcript_spool_once(
            capture_spool=TranscriptCaptureSpool(args.capture_spool),
            ingress=IngressQueueClient(base_url=args.ingress_url, timeout_seconds=args.timeout_seconds),
            target_profile=args.target_profile,
            max_items=args.max_items,
            requeue_recoverable_quarantine=args.requeue_recoverable_quarantine,
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] in {"idle", "queued", "requeued"} else 1

    parser.print_help()
    return 0
