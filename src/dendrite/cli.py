from __future__ import annotations

from argparse import ArgumentParser
import json

from . import __version__
from .provider_contracts import build_provider_doctor_report, build_provider_hook_plan


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
    provider = subparsers.add_parser("provider", help="provider hook planning utilities")
    provider_subparsers = provider.add_subparsers(dest="provider_command")
    provider_subparsers.add_parser("doctor", help="print provider contract readiness")
    hook_plan = provider_subparsers.add_parser("hook-plan", help="print a non-mutating provider hook plan")
    hook_plan.add_argument("--provider", required=True, choices=["claude", "gemini", "codex", "antigravity"])
    hook_plan.add_argument("--action", required=True, choices=["install", "uninstall"])
    hook_plan.add_argument("--project", default="<project>")
    hook_plan.add_argument("--capture-spool", default="<private-transcript-capture-spool>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.show_boundary:
        print("provider hook -> locator-only spool -> thin shipper -> POST 18080")
        return 0

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

    parser.print_help()
    return 0
