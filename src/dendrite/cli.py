"""CLI entry point."""
from __future__ import annotations

import json
import subprocess  # noqa: F401 -- monkeypatch surface (dendrite.cli.subprocess)
import sys  # noqa: F401 -- monkeypatch surface

from .cli_handlers import (
    capture_event_from_fixture,
    capture_event_from_stdin,
    capture_from_stdin,
    print_json,
    transcript_migrate,
)
from .cli_parser import build_parser
from .provider_contracts import build_provider_doctor_report, build_provider_hook_plan
from .source_catalog import resolve_source_ref, scan_source_catalog
from .transcript_capture import TranscriptCaptureSpool
from .transcript_drain import drain_transcript_spool_once
from .transcript_ingest import IngressQueueClient


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
        return capture_event_from_fixture(args)

    if args.command == "capture":
        return capture_event_from_stdin(args)

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
        return capture_from_stdin(args)

    if args.command == "transcript-migrate":
        return transcript_migrate(args)

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
            print_json({"status": "error", "error_class": "once_required"})
            return 2
        report = drain_transcript_spool_once(
            capture_spool=TranscriptCaptureSpool(args.capture_spool),
            ingress=IngressQueueClient(base_url=args.ingress_url, timeout_seconds=args.timeout_seconds),
            target_profile=args.target_profile,
            max_items=args.max_items,
            requeue_recoverable_quarantine=args.requeue_recoverable_quarantine,
        )
        print_json(report)
        return 0 if report["status"] in {"idle", "queued", "requeued"} else 1

    parser.print_help()
    return 0
