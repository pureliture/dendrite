"""CLI command handlers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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
from .transcript_migrate import migrate, parse_source_root_overrides


def best_effort_kickstart_launchagent(label: str) -> None:
    subprocess.run(
        ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=False,
    )


def print_json(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def print_capture_event_result(path) -> None:
    stored = json.loads(path.read_text(encoding="utf-8"))
    print_json(
        {
            "schema_version": "dendrite_capture_result.v1",
            "status": "spooled",
            "event_id": stored["event_id"],
            "provider": stored["provider"],
            "project": stored["project"],
            "event_type": stored["event_type"],
            "content_hash": stored["content_hash"],
            "spool_file": str(path),
        }
    )


def capture_event_from_fixture(args) -> int:
    try:
        payload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("fixture payload must be a JSON object")
        path = capture_event(payload, spool_root=args.spool)
        print_capture_event_result(path)
        return 0
    except Exception as exc:
        print_json({"status": "capture_error", "error_class": exc.__class__.__name__})
        return 1


def capture_event_from_stdin(args) -> int:
    if not args.stdin_json:
        print_json({"status": "error", "error_class": "stdin_json_required"})
        return 0 if args.non_fatal else 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("provider payload must be a JSON object")
        payload = {**payload, "provider": args.provider, "project": args.project}
        path = capture_event(payload, spool_root=args.spool)
        print_capture_event_result(path)
        return 0
    except Exception as exc:
        print_json({"status": "capture_error", "error_class": exc.__class__.__name__})
        return 0 if args.non_fatal else 1


def capture_from_stdin(args) -> int:
    if not args.stdin_json:
        print_json({"status": "error", "error_class": "stdin_json_required"})
        return 0 if args.non_fatal else 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("provider payload must be a JSON object")
        if args.require_workspace_path and not has_workspace_path(payload):
            print_json(
                {
                    "schema_version": "dendrite_transcript_capture_result.v1",
                    "status": "skipped_no_workspace_path",
                    "provider": args.provider,
                }
            )
            return 0
        if args.provider == "hermes" and args.hermes_profile:
            payload = {**payload, "hermes_profile": args.hermes_profile}
        request = normalize_provider_capture_request(args.provider, payload, project=args.project)
        path = TranscriptCaptureSpool(args.spool).enqueue(request)
        if args.kickstart_label:
            best_effort_kickstart_launchagent(args.kickstart_label)
        source_locator = request.get("source_locator") or {}
        print_json(
            {
                "schema_version": "dendrite_transcript_capture_result.v1",
                "status": "spooled",
                "request_id": request["request_id"],
                "provider": request["provider"],
                "project": request["project"],
                "event_type": request["event_type"],
                "source_locator_hash": source_locator.get("locator_hash", ""),
                "spool_file": str(path),
            }
        )
        return 0
    except Exception as exc:
        print_json(
            {
                "schema_version": "dendrite_transcript_capture_result.v1",
                "status": "capture_error",
                "error_class": exc.__class__.__name__,
            }
        )
        return 0 if args.non_fatal else 1


def transcript_migrate(args) -> int:
    try:
        roots = parse_source_root_overrides(args.source_root)
        report = migrate(
            spool_root=args.spool,
            roots=roots,
            project=args.project,
            providers=args.provider,
            hermes_profiles=args.hermes_profile,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print_json(report)
        return 0
    except Exception as exc:
        print_json(
            {
                "schema_version": "dendrite_transcript_migrate_result.v1",
                "status": "migrate_error",
                "error_class": exc.__class__.__name__,
            }
        )
        return 1
