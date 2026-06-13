from __future__ import annotations

from argparse import ArgumentParser
import json
from pathlib import Path
import sys

from . import __version__
from .capture import capture_event
from .provider_contracts import build_provider_doctor_report, build_provider_hook_plan
from .transcript_capture import (
    TranscriptCaptureSpool,
    has_workspace_path,
    normalize_provider_capture_request,
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
    capture.add_argument(
        "--require-workspace-path",
        action="store_true",
        help="skip capture when provider payload has no usable workspace path",
    )
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

    parser.print_help()
    return 0
