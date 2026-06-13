from __future__ import annotations

from argparse import ArgumentParser

from . import __version__


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

    parser.print_help()
    return 0
