"""spark — control tool for the brightroar model-serving stack."""

from __future__ import annotations

import argparse
import sys

from spark import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark", description=__doc__)
    parser.add_argument("--version", action="version", version=f"spark {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    from spark import leakcheck

    leakcheck.register(subparsers)
    from spark import docs

    docs.register(subparsers)
    from spark import launch

    launch.register(subparsers)
    from spark import brake

    brake.register(subparsers)
    from spark import status

    status.register(subparsers)
    from spark import render as render_cmd

    render_cmd.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
