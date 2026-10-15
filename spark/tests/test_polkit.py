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
# The seven services: llama-swap, the brake, compose, the pull, the front, the gate and the S05 drill's brake.
UNITS = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service", "local-ai-pull.service",
         "local-ai-front.service", "local-ai-gate.service", "local-ai-brake-drill.service"]
# Never on the rule: stopping the front's socket would free 9100 for any account to bind, and only OnFailure= starts
# the notifier.
SOCKETS = ["local-ai-front.socket", "local-ai-gate-status.socket", "local-ai-gate-control.socket"]
NOTIFIER = ["local-ai-notify@.service", "local-ai-notify@local-ai-gate.service",
            "local-ai-notify@local-ai-gate.service.service"]  # the template, and OnFailure='s %n instance

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

# Outside CI a missing node skips the three tests that run the rule. In CI (GitHub sets CI) it fails them, so a green CI
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
def test_spark_admin_starts_stops_and_restarts_the_seven_services():
    cases = [case(MANAGE, unit=unit, verb=verb) for unit in UNITS for verb in ("start", "stop", "restart")]
    assert ask(cases) == ["yes"] * len(cases)


@needs_node
def test_the_sockets_and_the_notifier_are_not_on_the_rule():
    # Not handled means polkit's default for the action: Dan's password (auth_admin), so a socket takes his password or
    # sudo, as the cutover starts them (Task 38).
    cases = [case(MANAGE, unit=unit, verb=verb) for unit in SOCKETS + NOTIFIER for verb in ("start", "stop", "restart")]
    assert ask(cases) == ["not handled"] * len(cases)


@needs_node
def test_every_other_verb_unit_or_action_is_left_to_polkits_default():
    # Not handled means polkit's default for the action: Dan's password (auth_admin).
    cases = [case(MANAGE, unit="local-ai-brake.service", verb=verb)
             for verb in ("reload", "try-restart", "reload-or-restart", "kill", "reset-failed", "set-property",
                          "freeze", "clean")]
    # systemd passes a unit's full name, so only the exact seven match: no prefix, no transient unit.
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


def test_the_rule_names_exactly_the_services_render_writes():
    # The rule lists the services by hand, in JavaScript; one added to render's POLKIT_UNITS must be added here too.
    from spark.render import POLKIT_UNITS, UNITS as RENDERED

    listed = re.search(r"var units = \[(.*?)\];", RULES.read_text(), re.DOTALL).group(1)
    assert re.findall(r'"([^"]+)"', listed) == list(POLKIT_UNITS) == UNITS
    assert set(UNITS + SOCKETS + NOTIFIER[:1]) == set(RENDERED)  # every unit render writes is one or the other


def test_in_ci_a_missing_node_fails_the_rule_tests_instead_of_skipping_them(tmp_path):
    # A green CI must mean the rule ran. The three tests that run it go to a pytest of their own, with no node on PATH:
    # in CI (GitHub sets CI) they must fail, and elsewhere skip. Each is named, so this test never runs itself.
    names = ("test_spark_admin_starts_stops_and_restarts_the_seven_services",
             "test_the_sockets_and_the_notifier_are_not_on_the_rule",
             "test_every_other_verb_unit_or_action_is_left_to_polkits_default")
    tests = [f"tests/test_polkit.py::{name}" for name in names]
    last = {}
    for ci in ("1", None):
        env = {"PATH": str(tmp_path), "HOME": str(tmp_path), **({"CI": ci} if ci else {})}
        run = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *tests], cwd=ROOT / "spark",
                             env=env, capture_output=True, text=True)
        last[ci] = run.stdout.splitlines()[-1] if run.stdout.strip() else run.stderr
    assert last["1"].startswith("3 failed") and last[None].startswith("3 skipped"), last
