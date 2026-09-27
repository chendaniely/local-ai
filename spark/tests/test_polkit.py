"""The polkit rule, run in a JavaScript engine with polkit, the action and the subject stubbed."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "stack/host/50-local-ai.rules"
MANAGE = "org.freedesktop.systemd1.manage-units"
UNITS = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service", "local-ai-pull.service"]

# Loads the rules file as polkitd does, with polkit's Result values (NOT_HANDLED is null), then asks
# every rule each case in turn, as polkitd asks them, until one answers. Prints one answer a case.
HARNESS = r"""
var fs = require("fs");
var rules = [];
var polkit = {
  Result: {NO: "no", YES: "yes", AUTH_SELF: "auth_self", AUTH_SELF_KEEP: "auth_self_keep",
           AUTH_ADMIN: "auth_admin", AUTH_ADMIN_KEEP: "auth_admin_keep", NOT_HANDLED: null},
  addRule: function (rule) { rules.push(rule); },
  log: function () {}
};
new Function("polkit", fs.readFileSync(process.argv[1], "utf8"))(polkit);
JSON.parse(process.argv[2]).forEach(function (c) {
  var action = {id: c.id, lookup: function (key) { return c.details[key]; }};
  var subject = {user: "someone", isInGroup: function (group) { return c.groups.indexOf(group) >= 0; }};
  var answer = null;
  for (var i = 0; i < rules.length && answer === null; i++) {
    var result = rules[i](action, subject);
    answer = result === undefined ? null : result;
  }
  console.log(answer === null ? "not handled" : answer);
});
"""

# Outside CI a missing node skips the two tests that run the rule. In CI (GitHub sets CI) it fails them, so a green CI
# means the rule ran. Read at import, before conftest clears the environment.
needs_node = pytest.mark.skipif(shutil.which("node") is None and not os.environ.get("CI"),
                                reason="no JavaScript engine (node) on PATH")


def case(action: str, groups=("spark-admin",), **details) -> dict:
    return {"id": action, "details": details, "groups": list(groups)}


def ask(cases: list[dict]) -> list[str]:
    out = subprocess.run(["node", "-e", HARNESS, str(RULES), json.dumps(cases)],
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


@needs_node
def test_spark_admin_starts_stops_and_restarts_the_four_units():
    cases = [case(MANAGE, unit=unit, verb=verb) for unit in UNITS for verb in ("start", "stop", "restart")]
    assert ask(cases) == ["yes"] * len(cases)


@needs_node
def test_every_other_verb_unit_or_action_is_left_to_polkits_default():
    # Not handled means polkit's default for the action: Dan's password (auth_admin).
    cases = [case(MANAGE, unit="local-ai-brake.service", verb=verb)
             for verb in ("reload", "try-restart", "reload-or-restart", "kill", "reset-failed", "set-property",
                          "freeze", "clean")]
    # systemd passes a unit's full name, so only the exact four match: no prefix, no transient unit.
    cases += [case(MANAGE, unit=unit, verb="start")
              for unit in ("ssh.service", "docker.service", "local-ai-probe.service", "local-ai-brake.service.d",
                           "xlocal-ai-brake.service", "local-ai-brake")]
    cases += [
        case(MANAGE, unit="local-ai-brake.service"),  # no verb
        case(MANAGE, verb="start"),  # no unit
        case("org.freedesktop.systemd1.manage-unit-files", unit="local-ai-brake.service", verb="start"),
        case("org.freedesktop.systemd1.reload-daemon"),
        case(MANAGE, groups=("spark-users",), unit="local-ai-brake.service", verb="start"),  # agent and spark
        case(MANAGE, groups=(), unit="local-ai-brake.service", verb="restart"),
    ]
    assert ask(cases) == ["not handled"] * len(cases)


def test_the_rule_names_exactly_the_units_render_writes():
    # The rule lists the units by hand, in JavaScript; a unit added to render must be added here too.
    from spark.render import UNITS as RENDERED

    listed = re.search(r"var units = \[(.*?)\];", RULES.read_text(), re.DOTALL).group(1)
    assert re.findall(r'"([^"]+)"', listed) == list(RENDERED) == UNITS


def test_in_ci_a_missing_node_fails_the_rule_tests_instead_of_skipping_them(tmp_path):
    # A green CI must mean the rule ran. The two tests that run it go to a pytest of their own, with no node on PATH:
    # in CI (GitHub sets CI) they must fail, and elsewhere skip. Each is named, so this test never runs itself.
    names = ("test_spark_admin_starts_stops_and_restarts_the_four_units",
             "test_every_other_verb_unit_or_action_is_left_to_polkits_default")
    tests = [f"tests/test_polkit.py::{name}" for name in names]
    last = {}
    for ci in ("1", None):
        env = {"PATH": str(tmp_path), "HOME": str(tmp_path), **({"CI": ci} if ci else {})}
        run = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *tests], cwd=ROOT / "spark",
                             env=env, capture_output=True, text=True)
        last[ci] = run.stdout.splitlines()[-1] if run.stdout.strip() else run.stderr
    assert last["1"].startswith("2 failed") and last[None].startswith("2 skipped"), last
