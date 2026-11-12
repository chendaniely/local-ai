"""The usage meter's first numbers (website/design/usage-meter-now.md): `spark usage`, a cost summary at Claude's
prices. This package holds the price files (mapping.yaml, prices.yaml, providers.yaml) and what reads them.

The front never imports it, so a change to these files restarts only the gate and the brake. At module level it
imports only the standard library, `yaml` and `spark.paths`, since `build_parser` imports every command's module."""

import argparse
import datetime as dt


def _since(text: str) -> float:
    """An ISO date, read as 00:00 UTC, or epoch seconds."""
    try:
        return float(text)
    except ValueError:
        pass
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is neither an ISO date like 2026-11-01 nor epoch seconds") from None
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()


def _rows(args: argparse.Namespace) -> int:
    import json

    from spark.meter import prices, pricing, store

    book = prices.load()
    rows = [r for r in store.read(book).rows if args.since is None or r.t0 >= args.since]
    for row in sorted(rows, key=lambda r: (r.t0, r.id)):
        print(json.dumps(pricing.row_json(row, pricing.price(book, row)), sort_keys=True))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    usage = subparsers.add_parser("usage", help="the usage meter: what the models would have cost at Claude's prices")
    verbs = usage.add_subparsers(dest="usage_command", required=True)
    rows = verbs.add_parser("rows", help="every counted row, priced, as one JSON line each, in time order")
    rows.add_argument("--since", type=_since, default=None, metavar="DATE|EPOCH",
                      help="only rows from this ISO date (00:00 UTC) or epoch second on")
    rows.set_defaults(func=_rows)
