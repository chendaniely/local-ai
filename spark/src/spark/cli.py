"""spark — control tool for the brightroar model-serving stack."""

from __future__ import annotations

import argparse
import sys

import yaml

from spark import __version__
from spark.gateclient import GateError


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
    from spark import apply

    apply.register(subparsers)
    from spark import models

    models.register(subparsers)
    from spark import clients

    clients.register(subparsers)
    from spark import doctor

    doctor.register(subparsers)
    from spark import commands

    commands.register(subparsers)
    gate = subparsers.add_parser("gate", help="run the gate on the two sockets systemd holds (local-ai-gate.service)")
    gate.set_defaults(func=_gate)
    front = subparsers.add_parser("front", help="run the front on 127.0.0.1:9100, the socket systemd holds "
                                                "(local-ai-front.service)")
    front.set_defaults(func=_front)
    return parser


def _gate(args: argparse.Namespace) -> int:
    # Imported here, as the gate runs: it brings uvicorn and Starlette, which `spark launch` and `spark status` never
    # import, since every command runs build_parser (Task 3's test).
    from spark.gate import main as gate_main

    return gate_main.run()


def _front(args: argparse.Namespace) -> int:
    # Imported here, as the front runs, for the same reason: it brings uvicorn and httpx.
    from spark.front import main as front_main

    return front_main.run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    # A refusal (RegistryError, RenderError, VersionsError, PullError), a file that can't be read, one that isn't
    # YAML, or a call to the gate that failed (a GateError: its text says the socket's state and the next step, and
    # never carries a key or the gate's body; the controller's ruling at Task 10): the message is the reason. A
    # command's own exit codes are returned above, untouched.
    except (GateError, ValueError, OSError, yaml.YAMLError) as err:
        print(f"spark {args.command}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
