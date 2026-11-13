"""spark.meter.summary and `spark usage summary`: the spend at hosted prices by day, month and all time, in Dan's
zone. Every row, price and file here is invented."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from spark import cli, paths
from spark.meter import prices, summary
from spark.meter.store import Row, Store

# The models these rows use pinned at Opus, so Dan's remaps of the shipped mapping never move these sums.
_SHIPPED = prices.load()
BOOK = dataclasses.replace(_SHIPPED, mapping={**_SHIPPED.mapping, **{
    name: prices.Mapping("opus", Decimal(1)) for name in ("qwen3.8-27b", "gemma-4-26b-a4b")},
    # unpriced on purpose here, so the shipped speech and embedding proxies never move these counts
    **{name: prices.Mapping("none", Decimal(1)) for name in (
        "qwen3-embedding-0.6b", "whisper-large-v3-turbo", "qwen3-asr-0.6b-32k")}})
NY = "America/New_York"
LA = "America/Los_Angeles"  # its fall-back is on 2026-11-01
CONTRACT_KEYS = {"v", "made_at", "zone", "zone_from", "counter_start", "last_row_at", "periods", "by_model", "flags",
                 "coverage", "electricity", "break_even"}
_ids = iter(range(1, 10_000))


def utc(text: str) -> float:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc).timestamp()


def local(text: str, zone: str = NY) -> float:
    """A wall-clock time in the zone, so a test doesn't depend on which tzdata release says what the offset is."""
    return dt.datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone)).timestamp()


def row(t0: float, model: str | None = "qwen3.8-27b", p: int | None = 1_000_000, c: int | None = 900_000,
        o: int | None = 10_000, end: str = "done", **more) -> Row:
    """A front row; at Opus's line, the defaults cost $0.78."""
    return Row("front", f"{next(_ids):016x}", t0, t0 + 5, model, p, c, o, end, key="demo", **more)


def make(rows, now, zone=NY, price=None, *, hours=False, **store) -> dict:
    return summary.summarize(Store(list(rows), **store), BOOK, summary.Private(price, zone), now, hours=hours)


def test_periods_follow_the_zone():
    now = local("2026-11-12T12:00")
    rows = [row(local("2026-10-31T23:30")),  # 03:30 UTC on 2026-11-01
            row(local("2026-11-11T23:30")), row(local("2026-11-12T00:30")), row(local("2026-09-30T12:00"))]
    out = make(rows, now)
    periods = out["periods"]
    assert (periods["last_month"]["requests"], periods["this_month"]["requests"]) == (1, 2)
    assert (periods["today"]["requests"], periods["all_time"]["requests"]) == (1, 4)
    assert dt.datetime.fromtimestamp(rows[0].t0, dt.timezone.utc).day == 1  # the next day in UTC
    assert [periods[k]["label"] for k in ("today", "this_month", "last_month", "all_time")] == [
        "2026-11-12", "2026-11", "2026-10", "all"]
    assert (out["zone"], out["zone_from"]) == (NY, "private_file")


def test_last_month_of_january_is_december():
    out = make([row(local("2026-12-31T23:00")), row(local("2027-01-01T01:00"))], local("2027-01-15T12:00"))
    assert out["periods"]["last_month"]["label"] == "2026-12"
    assert out["periods"]["last_month"]["requests"] == out["periods"]["this_month"]["requests"] == 1


def test_dst():
    z = ZoneInfo(LA)
    assert z.utcoffset(dt.datetime(2026, 10, 31)) != z.utcoffset(dt.datetime(2026, 11, 2))  # the fall-back is there
    rows = [row(utc("2026-11-01T06:59")),  # 23:59 PDT, 31 October
            row(utc("2026-11-01T07:30")),  # 00:30 PDT, 1 November
            row(utc("2026-11-01T08:30")), row(utc("2026-11-01T09:30")),  # 01:30 PDT, then 01:30 PST again
            row(utc("2026-11-02T07:30"))]  # 23:30 PST, still 1 November (00:30 the 2nd at PDT's offset)
    periods = make(rows, utc("2026-11-02T07:45"), LA)["periods"]
    assert periods["today"]["label"] == "2026-11-01" and periods["today"]["requests"] == 4
    assert (periods["this_month"]["requests"], periods["last_month"]["requests"]) == (4, 1)
    later = make(rows + [row(utc("2026-11-02T08:30"))], utc("2026-11-02T09:00"), LA)["periods"]
    assert (later["today"]["label"], later["today"]["requests"]) == ("2026-11-02", 1)


def test_usd_includes_the_estimate():
    t = local("2026-11-05T10:00")
    reported = row(t)  # $0.78
    estimated = dataclasses.replace(row(t + 60, p=500_000, c=400_000, o=5_000), source="opencode", key=None,
                                    provider="spark-sglang", session="a" * 16, c_estimated=True)  # $0.58
    this = make([reported, estimated], local("2026-11-06T10:00"))["periods"]["this_month"]
    assert (this["usd"], this["usd_estimated"]) == (1.36, 0.58)
    assert (this["requests"], this["p"], this["c"], this["o"], this["unpriced"]) == (2, 1_500_000, 1_300_000, 15_000, 0)
    assert isinstance(this["usd"], float)


def test_unpriced_and_flags():
    t = local("2026-11-05T10:00")
    rows = [row(t, end="error"), row(t, end="cut"), row(t, end="gone"), row(t, "made-up-model"),
            row(t, "qwen3-embedding-0.6b", p=7_000, c=0, o=0), row(t, c=None, dropped=2),
            row(local("2026-10-05T10:00"), "made-up-model"), row(local("2026-10-05T10:00"), end="error")]
    out = make(rows, local("2026-11-06T10:00"), bad_lines=3)
    this = out["periods"]["this_month"]
    assert (this["requests"], this["unpriced"], this["usd"]) == (6, 2, 0.0)
    assert (this["p"], this["c"], this["o"]) == (5_007_000, 3_600_000, 50_000)  # the embedding's 7,000 in p
    assert out["flags"] == {"unmapped": {"made-up-model": 1}, "interrupted": 2, "errors": 1, "cache_unknown": 1,
                            "dropped": 2, "bad_lines": 3}
    without = make(rows[:4] + rows[5:], local("2026-11-06T10:00"), bad_lines=3)
    assert without["flags"] == out["flags"]
    alone = without["periods"]["this_month"]
    assert (alone["unpriced"], alone["usd"], alone["usd_estimated"]) == (this["unpriced"], this["usd"],
                                                                          this["usd_estimated"])
    assert (alone["requests"], alone["p"], alone["c"], alone["o"]) == (5, 5_000_000, 3_600_000, 50_000)


def test_a_transcription_is_not_unpriced():
    # Speech rows carry no token counts, and their models are `none`: unpriced on purpose, never "N unpriced"; a chat
    # row with no counts (a route the front doesn't scan) is unpriced (the final review's I1).
    t = local("2026-11-05T10:00")
    rows = [row(t, "whisper-large-v3-turbo", p=None, c=None, o=None),
            row(t, "qwen3-asr-0.6b-32k", p=None, c=None, o=None), row(t, p=None, c=None, o=None)]
    this = make(rows, local("2026-11-06T10:00"))["periods"]["this_month"]
    assert (this["requests"], this["unpriced"], this["usd"]) == (3, 1, 0.0)


def test_share_of_price(tmp_path):
    rows = [row(local("2026-10-05T10:00"), p=0, c=0, o=1_000_000), row(local("2026-11-05T10:00"), p=0, c=0,
                                                                         o=1_000_000)]  # $20 each
    out = make(rows, local("2026-11-06T10:00"), price=Decimal("4000"))
    assert (out["periods"]["all_time"]["usd"], out["periods"]["all_time"]["share_of_price"]) == (40.0, 0.01)
    assert "share_of_price" not in out["periods"]["this_month"]
    private = summary.read_private(tmp_path / "absent.yaml")
    assert private == summary.Private(None, None)
    bare = summary.summarize(Store(rows), BOOK, private, local("2026-11-06T10:00"))
    assert bare["periods"]["all_time"]["share_of_price"] is None and bare["zone_from"] == "system"


def test_read_private(tmp_path):
    path = tmp_path / "meter.yaml"
    path.write_text("price_usd: 1234.5\nzone: Europe/Paris\nkwh_usd: 0.2\n")
    assert summary.read_private(path) == summary.Private(Decimal("1234.5"), "Europe/Paris")
    path.write_text("")
    assert summary.read_private(path) == summary.Private(None, None)


def test_private_file_errors_never_show_values(tmp_path):
    path = tmp_path / "meter.yaml"
    cases = [("price_usd: -5\n", "price_usd", "-5"), ("price_usd: 0\n", "price_usd", None),
             ("price_usd: lots\n", "price_usd", "lots"), ("price_usd: true\n", "price_usd", "True"),
             ("zone: Mars/Olympus\n", "zone", "Mars"), ("zone: 7\n", "zone", None),
             ("price_usd: [98765.4\n", "YAML", "98765"), ("- 98765.4\n", "mapping", "98765")]
    for text, named, hidden in cases:
        path.write_text(text)
        with pytest.raises(ValueError) as err:
            summary.read_private(path)
        message = str(err.value)
        assert named in message and str(path) in message, text
        if hidden is not None:
            assert hidden not in message.replace(str(path), ""), text
        context = err.value.__context__ is None or err.value.__suppress_context__
        assert err.value.__cause__ is None and context, text  # no traceback shows the YAML error's excerpt


def test_a_private_file_that_cannot_be_read(tmp_path):
    path = tmp_path / "meter.yaml"
    path.mkdir()
    (path / "price_usd").write_text("31415")
    with pytest.raises(ValueError) as err:
        summary.read_private(path)
    message = str(err.value)
    assert str(path) in message and "can't be read" in message
    assert "31415" not in message.replace(str(path), "")


def test_zone_of(tmp_path):
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/Europe/Paris")  # never read: only the link's text is
    zone, name, source = summary.zone_of(summary.Private(), link)
    assert (name, source) == ("Europe/Paris", "system") and zone == ZoneInfo("Europe/Paris")
    assert summary.zone_of(summary.Private(zone=LA), link)[1:] == (LA, "private_file")
    for target in ("/nowhere/at/all", "/usr/share/zoneinfo/Mars/Olympus"):
        other = tmp_path / f"link{len(target)}"
        other.symlink_to(target)
        assert summary.zone_of(summary.Private(), other) == (None, "localtime", "system")
    plain = tmp_path / "plain"
    plain.write_text("not a link")
    assert summary.zone_of(summary.Private(), plain) == (None, "localtime", "system")


def test_localtime_follows_the_system_instant_by_instant(tmp_path, monkeypatch):
    """With no zone name to be had, each row is placed by the system's rules at its own instant: a row from summer
    time, read in winter, keeps its date, and so does the month's start for counting's gaps."""
    plain = tmp_path / "plain"
    plain.write_text("not a link")
    monkeypatch.setattr(summary, "LOCALTIME", plain)
    rows = [row(utc("2026-11-01T06:59")),  # 23:59 PDT, 31 October
            row(utc("2026-11-01T07:30"))]  # 00:30 PDT, 1 November: 23:30 on the 31st at PST's offset
    spans = [(utc("2026-11-01T06:40"), utc("2026-11-01T06:50")),  # ends 23:50 PDT, 31 October
             (utc("2026-11-01T07:00"), utc("2026-11-01T07:10"))]  # ends 00:10 PDT, 1 November
    try:
        with monkeypatch.context() as patch:
            patch.setenv("TZ", LA)
            time.tzset()
            out = summary.summarize(Store(rows, counting_off=spans), BOOK, summary.Private(),
                                    utc("2026-11-15T20:00"))  # noon PST
    finally:
        time.tzset()  # the context has put TZ back
    assert (out["zone"], out["zone_from"]) == ("localtime", "system")
    periods = out["periods"]
    assert (periods["this_month"]["label"], periods["this_month"]["requests"]) == ("2026-11", 1)
    assert (periods["last_month"]["requests"], periods["today"]["label"]) == (1, "2026-11-15")
    assert out["coverage"]["counting_off"] == [[utc("2026-11-01T07:00"), utc("2026-11-01T07:10")]]


def test_by_model_sorted():
    t = local("2026-11-05T10:00")
    rows = [row(t, "gemma-4-26b-a4b", o=1_000), row(t, "qwen3.8-27b", o=500_000), row(t, "gemma-4-26b-a4b", o=1_000),
            row(local("2026-10-05T10:00"), "gemma-4-26b-a4b", p=0, c=0, o=10_000_000)]
    by_model = make(rows, local("2026-11-06T10:00"))["by_model"]
    month = [m for m in by_model if m["period"] == "this_month"]
    assert [m["model"] for m in month] == ["qwen3.8-27b", "gemma-4-26b-a4b"]
    assert month[0]["usd"] > month[1]["usd"] and month[1]["requests"] == 2 and month[1]["tier"] == "opus"
    assert set(month[0]) == {"model", "tier", "period", "usd", "usd_estimated", "requests", "p", "c", "o",
                           "audio_s"}
    assert [m["model"] for m in by_model if m["period"] == "all_time"] == ["gemma-4-26b-a4b", "qwen3.8-27b"]
    assert by_model[:2] == month
    unmapped = make([row(t, "made-up-model")], local("2026-11-06T10:00"))["by_model"][0]
    assert unmapped["tier"] is None and unmapped["usd"] == 0.0


def test_no_model_is_spelled_out():
    t = local("2026-11-05T10:00")
    out = make([row(t, None), row(t, None, end="refused"), row(t)], local("2026-11-06T10:00"))
    month = [m for m in out["by_model"] if m["period"] == "this_month"]
    assert [(m["model"], m["tier"], m["requests"]) for m in month] == [("qwen3.8-27b", "opus", 1),
                                                                       ("(no model)", None, 2)]
    assert out["flags"]["unmapped"] == {"(no model)": 1}  # the refused row would never have been priced
    assert None not in {m["model"] for m in out["by_model"]}


def test_hours_only_when_asked():
    t = utc("2026-11-05T10:05")
    rows = [row(t), row(t + 600), row(t + 3600), row(t, "gemma-4-26b-a4b")]
    assert "hours" not in make(rows, utc("2026-11-06T10:00"))
    hours = make(rows, utc("2026-11-06T10:00"), hours=True)["hours"]
    first = hours[0]
    assert first == {"hour": int(utc("2026-11-05T10:00")), "source": "front", "key": "demo", "provider": None,
                     "model": "gemma-4-26b-a4b", "usd": 0.78, "requests": 1, "p": 1_000_000, "c": 900_000,
                     "o": 10_000}
    pair = hours[1]
    assert (pair["model"], pair["requests"], pair["usd"], pair["p"], pair["o"]) == (
        "qwen3.8-27b", 2, 1.56, 2_000_000, 20_000)
    assert [h["hour"] for h in hours] == sorted(h["hour"] for h in hours) and len(hours) == 3


def test_coverage_and_times():
    month_start = local("2026-11-01T00:00")
    spans = [(month_start - 100, month_start - 50), (month_start - 10, month_start + 10), (month_start + 500, None)]
    rows = [row(local("2026-11-05T10:00")),
            dataclasses.replace(row(local("2026-10-03T10:00")), source="opencode", key=None, provider="spark-sglang"),
            row(local("2026-10-04T10:00"))]
    out = make(rows, local("2026-11-06T10:00"), counting_off=spans, last_import=month_start + 7, imported=12)
    assert out["coverage"] == {"counting_off": [[month_start - 10, month_start + 10], [month_start + 500, None]],
                               "imported": {"opencode": {"last_import": month_start + 7, "rows": 12}},
                               "not_counted": ["by-hand engines: counted from heartsbane's OpenCode only"]}
    assert (out["counter_start"], out["last_row_at"]) == (rows[2].t0, rows[0].t0)
    assert (out["made_at"], out["v"], out["electricity"], out["break_even"]) == (local("2026-11-06T10:00"), 1, None,
                                                                                 None)


def test_empty_store():
    out = summary.summarize(Store(), BOOK, summary.Private(), utc("2026-11-06T10:00"))
    assert set(out) == CONTRACT_KEYS
    for period in out["periods"].values():
        assert {k: period[k] for k in ("usd", "usd_estimated", "requests", "p", "c", "o", "unpriced")} == {
            "usd": 0.0, "usd_estimated": 0.0, "requests": 0, "p": 0, "c": 0, "o": 0, "unpriced": 0}
    assert (out["counter_start"], out["last_row_at"], out["by_model"]) == (None, None, [])
    assert out["flags"] == {"unmapped": {}, "interrupted": 0, "errors": 0, "cache_unknown": 0, "dropped": 0,
                            "bad_lines": 0}
    assert summary.words(out).splitlines()[1].startswith("This month")


def test_words():
    out = make([row(local("2026-11-05T10:00"))], local("2026-11-06T10:00"), price=Decimal("78"))
    text = summary.words(out)
    lines = {line.split("  ")[0]: line for line in text.splitlines()}
    assert "$0.78" in lines["This month"] and "1.0M in" in lines["This month"] and "10.0k out" in lines["This month"]
    assert "1.0 %" in lines["All time"]
    assert "—" in summary.words(make([], local("2026-11-06T10:00"))).splitlines()[-1]


def test_words_print_the_flags_and_counting_off():
    # make usage prints this month's flags in the menu's words and every counting-off span with its date, in the
    # summary's zone (the final review's I4): S26 says so.
    t = local("2026-11-05T10:00")
    rows = [row(t, end="cut"), row(t, end="gone"), row(t, end="error"), row(t, "made-up-model"),
            row(t, "other-model"), row(t, c=None, dropped=2), row(t, p=None)]
    spans = [(local("2026-11-02T13:58"), local("2026-11-02T14:01")),
             (local("2026-11-03T23:58"), local("2026-11-04T00:01")), (local("2026-11-12T08:52"), None)]
    out = make(rows, local("2026-11-12T12:00"), counting_off=spans)
    lines = summary.words(out).splitlines()
    assert lines[5:] == [
        "⚠map made-up-model, other-model · 2 interrupted · 1 errors · 4 unpriced · 1 cache unknown · 2 dropped",
        "counting off 2026-11-02 13:58–14:01",
        "counting off 2026-11-03 23:58–2026-11-04 00:01",
        "counting off since 2026-11-12 08:52"]
    quiet = make([row(t)], local("2026-11-12T12:00"))
    assert len(summary.words(quiet).splitlines()) == 5  # nothing set: no flags line, no span
    only_span = make([row(t)], local("2026-11-12T12:00"), counting_off=spans[2:])
    assert summary.words(only_span).splitlines()[5:] == ["counting off since 2026-11-12 08:52"]


# Every key spark-menu reads from the summary (its lib/usage.sh's poller and lib/panel.sh's panel_usage, in Dan's
# dotfiles), with the JSON type it expects: a rename or a retype here must fail in this repo, not in the menu (the final
# review's recommendation 2). A change to this list is a contract change: website/reference/projects.md's row, and a
# notice/spark-menu issue, by CLAUDE.md's sync table.
MENU_READS = [
    (("v",), int), (("zone",), str), (("made_at",), float),
    *[(("periods", k, f), t) for k in ("this_month", "today", "last_month", "all_time")
      for f, t in (("label", str), ("usd", float), ("p", int), ("o", int), ("usd_estimated", float))],
    (("periods", "all_time", "share_of_price"), float), (("periods", "this_month", "unpriced"), int),
    (("flags", "unmapped"), dict), (("flags", "interrupted"), int), (("flags", "errors"), int),
    (("flags", "cache_unknown"), int), (("flags", "dropped"), int),
    (("coverage", "counting_off"), list), (("coverage", "imported", "opencode", "last_import"), float),
]
MENU_READS_BY_MODEL = {"period": str, "model": str, "tier": str, "usd": float, "p": int, "o": int}


def test_every_key_spark_menu_reads():
    t = local("2026-11-05T10:00")
    out = json.loads(json.dumps(make([row(t)], local("2026-11-06T10:00"), price=Decimal("78"),
                                     counting_off=[(t - 60, t - 30), (t, None)], last_import=t + 1, imported=1)))
    for path, kind in MENU_READS:
        value = out
        for key in path:
            assert isinstance(value, dict) and key in value, path
            value = value[key]
        assert isinstance(value, kind) and not isinstance(value, bool), path
    assert out["v"] == 1
    assert out["coverage"]["counting_off"] == [[t - 60, t - 30], [t, None]]  # [start, end], end null while off
    entry = next(m for m in out["by_model"] if m["period"] == "this_month")
    for key, kind in MENU_READS_BY_MODEL.items():
        assert isinstance(entry.get(key), kind), key


def test_summary_verb(tmp_path, monkeypatch, capsys):
    usage = tmp_path / "u"
    usage.mkdir()
    lines = [{"v": 1, "id": f"{i:016x}", "t0": t, "t1": t + 5, "key": "demo", "model": "qwen3.8-27b", "end": "done",
              "p": 1000, "c": 500, "o": 10} for i, t in enumerate([utc("2026-10-30T00:00"), utc("2026-11-02T00:00")])]
    bad_times = [{**lines[0], "id": f"{9 - i:016x}", "t0": t0} for i, t0 in enumerate([float("nan"), float("inf"),
                                                                                         1.79e12])]  # a ms stamp
    (usage / "usage-2026-11.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines + bad_times))
    monkeypatch.setattr(paths, "USAGE_DIR", usage)
    monkeypatch.setattr(paths, "USAGE_SESSIONS_DIR", tmp_path / "s")
    monkeypatch.setattr(paths, "GATE_LEDGER_DIR", tmp_path / "l")
    monkeypatch.setattr(paths, "METER_PRIVATE", tmp_path / "meter.yaml")
    assert cli.main(["usage", "summary", "--json"]) == 0
    printed = capsys.readouterr().out
    assert len(printed.strip().splitlines()) == 1
    out = json.loads(printed, parse_constant=pytest.fail)  # strict JSON: no NaN or Infinity
    assert out["v"] == 1 and set(out) == CONTRACT_KEYS and out["periods"]["all_time"]["requests"] == 2
    assert out["flags"]["bad_lines"] == 3
    assert cli.main(["usage", "summary", "--json", "--hours"]) == 0
    assert len(json.loads(capsys.readouterr().out)["hours"]) == 2
    with pytest.raises(SystemExit) as stopped:
        cli.main(["usage", "summary", "--hours"])
    assert stopped.value.code == 2 and "--hours" in capsys.readouterr().err
    assert cli.main(["usage", "summary"]) == 0
    starts = [line.split("  ")[0] for line in capsys.readouterr().out.splitlines()]
    assert {"This month", "Today", "Last month", "All time"} <= set(starts)
    (tmp_path / "meter.yaml").write_text("price_usd: -31415\n")
    assert cli.main(["usage", "summary", "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "price_usd" in captured.err and "31415" not in captured.err


WHISPER = dataclasses.replace(BOOK, mapping={**BOOK.mapping, "whisper-large-v3-turbo": prices.Mapping(
    "whisper-1", Decimal(1))})


def speech_rows():
    t = local("2026-11-05T10:00")
    return [Row("front", f"{next(_ids):016x}", t, t + 5, "whisper-large-v3-turbo", None, None, None, "done",
                key="demo", **more) for more in ({"audio_s": 90.0}, {"audio_s": 100.0, "audio_est": True},
                                                  {}, {"audio_s": 30.0}) ] + [row(t)]


def test_audio_is_summed_priced_or_not_and_estimates_are_counted():
    out = summary.summarize(Store(speech_rows()), WHISPER, summary.Private(None, NY), local("2026-11-06T10:00"))
    month = out["periods"]["this_month"]
    assert month["audio_s"] == 220.0 and out["periods"]["last_month"]["audio_s"] == 0.0
    # 90 s + 100 s + 30 s = 220 s at 0.006 a minute is 0.022; the 100 s row is the estimated one
    assert month["usd"] == pytest.approx(0.78 + 0.022, abs=1e-4)
    assert month["usd_estimated"] == pytest.approx(0.01, abs=1e-4)
    assert month["unpriced"] == 1  # the row with no length
    assert out["flags"]["unmapped"] == {}


def test_a_row_with_both_estimates_counts_once():
    t = local("2026-11-05T10:00")
    both = row(t, c=900_000, c_estimated=True, audio_s=60.0, audio_est=True)
    assert make([both], local("2026-11-06T10:00"))["periods"]["this_month"]["usd_estimated"] == 0.78


def test_words_show_audio_minutes_and_name_both_estimates():
    out = summary.summarize(Store(speech_rows()[:1]), WHISPER, summary.Private(None, NY), local("2026-11-06T10:00"))
    lines = {line.split("  ")[0]: line for line in summary.words(out).splitlines()}
    assert "1.5 min audio" in lines["This month"] and "min audio" not in lines["Today"]
    est = summary.summarize(Store(speech_rows()[1:2]), WHISPER, summary.Private(None, NY), local("2026-11-06T10:00"))
    assert "of it estimated" in summary.words(est) and "estimated cache" not in summary.words(est)
    assert "audio" not in summary.words(make([row(local("2026-11-05T10:00"))], local("2026-11-06T10:00")))


def test_an_unlengthed_speech_row_counts_as_unpriced_and_an_error_row_adds_no_audio():
    t = local("2026-11-05T10:00")
    rows = [Row("front", f"{next(_ids):016x}", t, t + 5, "whisper-large-v3-turbo", None, None, None, "done"),
            Row("front", f"{next(_ids):016x}", t, t + 5, "whisper-large-v3-turbo", None, None, None, "error",
                audio_s=600.0)]
    month = summary.summarize(Store(rows), WHISPER, summary.Private(None, NY), local("2026-11-06T10:00"))[
        "periods"]["this_month"]
    assert month["unpriced"] == 1 and month["audio_s"] == 0.0


def test_an_estimated_length_on_a_per_token_line_is_not_an_estimated_dollar():
    t = local("2026-11-05T10:00")
    odd = row(t, audio_s=60.0, audio_est=True)  # Opus's per-token line, a length that prices nothing
    assert make([odd], local("2026-11-06T10:00"))["periods"]["this_month"]["usd_estimated"] == 0.0


def test_words_leave_audio_out_of_a_period_with_none():
    out = summary.summarize(Store(speech_rows()[:1]), WHISPER, summary.Private(None, NY), local("2026-11-06T10:00"))
    lines = {line.split("  ")[0]: line for line in summary.words(out).splitlines()}
    assert "min audio" in lines["This month"] and "min audio" not in lines["Last month"]
