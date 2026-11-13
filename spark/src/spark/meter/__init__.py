"""The usage meter's first numbers (website/design/usage-meter-now.md): `spark usage`, a cost summary at Claude's
prices. This package holds the price files (mapping.yaml, prices.yaml, providers.yaml) and what reads them.

The front never imports it, so a change to these files restarts only the gate and the brake. At module level it
imports only the standard library, `yaml` and `spark.paths`, since `build_parser` imports every command's module."""

import argparse
import datetime as dt
import math


def _since(text: str) -> float:
    """An ISO date, read as 00:00 UTC, or finite epoch seconds. The date is tried first, so 20261101 is a day."""
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        pass
    else:
        return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()
    try:
        epoch = float(text)
    except ValueError:
        epoch = math.nan
    if not math.isfinite(epoch):
        raise argparse.ArgumentTypeError(f"{text!r} is neither an ISO date like 2026-11-01 nor epoch seconds")
    return epoch


def _rows(args: argparse.Namespace) -> int:
    import json

    from spark.meter import prices, pricing, store

    book = prices.load()
    rows = [r for r in store.read(book).rows if args.since is None or r.t0 >= args.since]
    for row in sorted(rows, key=lambda r: (r.t0, r.id)):
        print(json.dumps(pricing.row_json(row, pricing.price(book, row)), sort_keys=True))
    return 0


def _summary(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    import json
    import time

    from spark.meter import prices, store, summary

    if args.hours and not args.json:
        parser.error("--hours needs --json: the hours are only in the JSON")
    private = summary.read_private()  # first, so a private file that can't be used prints nothing on stdout
    book = prices.load()
    made = summary.summarize(store.read(book), book, private, time.time(), hours=args.hours)
    # allow_nan=False: spark-menu reads strict JSON, so a NaN or Infinity from a file the store doesn't check (the
    # ledger, the import's mark) is refused here, and spark-menu keeps its last good answer.
    print(json.dumps(made, allow_nan=False) if args.json else summary.words(made))
    return 0


REFUSED_EXIT = 3  # `spark usage import` stored the good rows but refused some, or refused the batch whole


def _import(args: argparse.Namespace) -> int:
    """Exit 0 when nothing was refused, else REFUSED_EXIT after storing the good rows: a push with refusals fails
    loudly, so the Mac keeps its watermark and sends them again (the final review's I2, 2026-11-12)."""
    import sys
    import time

    from spark.meter import importer

    verdict = importer.run(sys.stdin.buffer, time.time())
    print(verdict.line())
    return REFUSED_EXIT if verdict.refused else 0


def _prices(args: argparse.Namespace) -> int:
    from spark.meter import prices

    print(prices.table(prices.load()))
    return 0


def _harvest(args: argparse.Namespace) -> int:
    import json
    import sys
    from pathlib import Path

    from spark.meter import harvest, prices

    db = Path(args.db) if args.db else harvest.default_db()
    stats: dict = {}
    count = 0
    for row in harvest.harvest(db, prices.by_hand(prices.load(), "opencode"), args.since, stats):
        print(json.dumps(row, sort_keys=True, allow_nan=False))
        count += 1
    skipped = stats.get("skipped", 0)
    print(f"harvest: {count} rows" + (f", skipped {skipped}" if skipped else ""), file=sys.stderr)
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    usage = subparsers.add_parser("usage", help="the usage meter: what the models would have cost at hosted prices")
    verbs = usage.add_subparsers(dest="usage_command", required=True)
    rows = verbs.add_parser("rows", help="every counted row, priced, as one JSON line each, in time order")
    rows.add_argument("--since", type=_since, default=None, metavar="DATE|EPOCH",
                      help="only rows from this ISO date (00:00 UTC) or epoch second on")
    rows.set_defaults(func=_rows)
    summary = verbs.add_parser("summary", help="today, this month, last month and all time at hosted prices, in "
                                               "Dan's zone")
    summary.add_argument("--json", action="store_true", help="the summary spark-menu reads (its contract, v1), as one "
                                                             "JSON object")
    summary.add_argument("--hours", action="store_true", help="with --json, add the totals per UTC hour, key and model")
    summary.set_defaults(func=lambda args: _summary(args, summary))
    prices_verb = verbs.add_parser("prices", help="every model's pricing proxy and price, as the table the model "
                                                  "prices page holds")
    prices_verb.set_defaults(func=_prices)
    imp = verbs.add_parser("import", help="merge the Mac's per-message rows for the by-hand engines, read from stdin")
    imp.add_argument("--source", required=True, choices=["opencode"], help="whose rows are on stdin")
    imp.set_defaults(func=_import)
    har = verbs.add_parser("harvest", help="on the Mac: OpenCode's per-message token rows for the by-hand engines, "
                                           "as JSON lines, for `spark usage import`")
    har.add_argument("--source", required=True, choices=["opencode"], help="whose database to read")
    har.add_argument("--db", default=None, metavar="PATH", help="OpenCode's database (default: its own place)")
    har.add_argument("--since", type=_since, default=None, metavar="DATE|EPOCH",
                     help="only messages updated from this ISO date (00:00 UTC) or epoch second on")
    har.set_defaults(func=_harvest)
