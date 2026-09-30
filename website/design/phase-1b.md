---
title: "Phase 1b — implementation plan"
description: "Orca and the web UI on the Mac: pi and Dan's Claude Code in Orca's panes, pi reaching the Spark over the tunnel, Open WebUI as a Mac app. Nothing changes on the box beyond the repo's own code."
date: 2026-09-30
---

# Phase 1b — Orca and the web UI on the Mac — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax. Every task is labelled **[Spark]**, **[Mac]** or **[Dan]**; ⇄ marks a
> machine switch (one session at a time). Phase 1 is merged into `main` (2026-09-29), and this
> plan's branch, `phase-1b`, starts from it. Written 2026-09-30 on the Mac, in a separate worktree
> (`~/git/hub/local-ai-phase-1b`), at Dan's request. Every code listing below, and every command
> the runbook in Task 3 gives, was run first: the code in a scratch copy of the branch (915 tests
> pass), the commands on the Mac.

**Goal:** Dan uses Orca on the Mac with his own Claude Code (no permission bypass, telemetry off)
and with pi talking to the Spark's models over `make tunnel`, and opens Open WebUI on the Mac as
an app of its own; `make doctor` fails if Orca ever connects to the Spark as Dan.

**Architecture:** Orca stays on the Mac, in its local mode: it launches `claude` and `pi` in
panes, and pi's `spark` provider reaches llama-swap through the same SSH tunnel as pi in a
terminal. The Spark changes only through the repo: the scenario check learns `phase: 1b`, and
`spark doctor` gains one check, `Orca relay`. The rest is documentation: a runbook for Orca on the
Mac, the Mac in the web UI's runbook, two scenario pages, and the record in README and the plan.

**Tech Stack:** Python 3.12 via uv (`spark` CLI, pytest) · Orca 1.4.216 (desktop, local mode) ·
pi (the Mac's, from Homebrew) · Open WebUI v0.11.4 behind `tailscale serve` · Safari web apps
(macOS 14 or later; the Mac runs 26.6.2) · Quarto for the site.

**Spec:** [`plan.md`](plan.md) — the *Claude is untouched* constraint, the *Orca* requirement,
the Orca rows in *Components* and *Visibility*, the Phase 1b entry, and the *Orca on the Mac* open
item (commits a6eb139 and 3397806).

## Global Constraints

- Nothing of Orca's runs on the Spark: neither `brightroar` nor `brightroar-agent` is ever an Orca
  target.
- Every Claude that Orca starts or resumes runs as it would from a terminal: no
  `--dangerously-skip-permissions` or other bypass, and nothing in Orca's per-agent environment
  setting for Claude.
- Reporting hooks exit 0 and print nothing or `{}`; an app that installs them adds only its own
  hook entries to `~/.claude/settings.json`; the app receiving the events passes none of them on
  (Orca with its telemetry off).
- Orca is recorded in `README.md` §Current state, not in `stack/versions.yaml`.
- Public repo: never the Spark's full tailnet name, host-key prompts, Orca access links or QR codes,
  screenshots of Orca, or Orca's data files (`orca-data.json`, spool, scrollback, relay logs).
- Every command in the docs says where it runs, in bold in the paragraph above its block; no `#` in
  a shell block that runs on the Mac.
- Markdown under `website/`: a mid-document horizontal rule is `***`, never `---`.
- Stage by explicit path; commits are Conventional Commits with 🤖 after the prefix and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; *Before every push*
  (`website/how-to/leak-guards.md`) before each push; pushes only with Dan's explicit OK.

## Review Focus

1. **An Orca update puts its defaults back** (the skip flag, telemetry on): Task 3's runbook checks
   after each update, and Task 5 runs those checks once.
2. **A resumed Claude session is started another way**, which could carry the bypass that a new one
   doesn't: Task 5 checks a resumed session as well as a new one.
3. **Orca writes a key other than its hooks into `~/.claude/settings.json`** (an endpoint, a
   permission mode, a status line): the runbook's before-and-after fingerprint of the file without
   `hooks`. Task 3 runs it twice to see it stable; the first Orca update after the phase is its
   real test.
4. **pi in Orca without the tunnel**: its requests fail to connect; S24's page and the runbook say
   to run `make tunnel`, and Task 5 sees pi answer only with it.
5. **A relay folder doctor can't look into**: the `Orca relay` check fails on "can't tell" rather
   than passing (Task 2's first test), and on the box it is seen failing against a planted folder.

***

## ⇄ Switch point — Mac → Spark

- [ ] The Mac session runs leak-guards.md's
  [*Before every push*](../how-to/leak-guards.md#before-every-push) with `phase-1b` as `<branch>`;
  then **Dan OKs the push**. **On the Mac**, from the worktree:
  `git push -u origin phase-1b`. **On the Spark**, in Dan's clone:
  `git fetch && git switch phase-1b`.

***

### Task 0 [Dan, Mac]: Orca's two settings — any time, before Task 5

- [ ] **Step 1:** In Orca's Settings: **Agent Permissions → Manual**, and **Privacy → Share
  anonymous usage data: off**. Leave **Agents → Agent status hooks** on.
- [ ] **Step 2: Check** — **on the Mac**:

```bash
jq -r '.settings | "telemetry: \(.telemetry.optedIn)", "claude args: [\(.agentDefaultArgs.claude // "")]", "claude env: \(.agentDefaultEnv.claude // {} | keys)"' "$HOME/Library/Application Support/orca/profiles/local-default/orca-data.json"
```

Expected: `telemetry: false`, `claude args: []`, `claude env: []`. (On 2026-09-30 it printed
`telemetry: true` and `claude args: [--dangerously-skip-permissions]`: Orca's defaults.) Give the
Mac session the date; it goes in README at Task 6.

***

### Task 1 [Spark]: the scenario check accepts `phase: 1b`; the S24 and S25 pages

**Files:**

- Modify: `spark/src/spark/docs.py` (the `STATUSES` line and the phase check in `check_scenarios`)
- Modify: `spark/tests/test_scenarios.py`
- Create: `website/scenarios/s24-pi-in-orca.md`, `website/scenarios/s25-chat-from-the-mac.md`

**Interfaces:** Produces `spark.docs.PHASES`, the set of accepted `phase` values:
`{1, "1b", 2, 3, 4, 5, "backlog"}`.

- [ ] **Step 1: Write the failing tests** — in `spark/tests/test_scenarios.py`, add `import pytest`
  above the existing import, so the file starts:

```python
import pytest

from spark.docs import check_scenarios
```

and append at the end of the file:

```python


def test_phase_1b_is_a_phase(tmp_path):
    # Phase 1b, Orca and the web UI on the Mac, sits between Phases 1 and 2. YAML reads `1b` as a string.
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace("phase: 2", "phase: 1b"))
    assert check_scenarios(tmp_path) == []


@pytest.mark.parametrize("phase", ["1c", "1.5", "b1", '"1"', "6"])
def test_other_phases_are_refused(tmp_path, phase):
    # A quoted "1" is a string, not Phase 1, and 1.5 is a float: neither is a phase.
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace("phase: 2", f"phase: {phase}"))
    assert any("phase must be" in p for p in check_scenarios(tmp_path))
```

- [ ] **Step 2: See it fail** — **on the Spark**:
  `uv run --frozen --project spark pytest spark/tests/test_scenarios.py -q`.
  Expected: `test_phase_1b_is_a_phase` FAILS with
  `'s01-morning-start.md: phase must be 1–5 or backlog'`; the five `test_other_phases_are_refused`
  cases pass already.
- [ ] **Step 3: Implement** — in `spark/src/spark/docs.py`, after the `STATUSES` line, add:

```python
PHASES = {1, "1b", 2, 3, 4, 5, "backlog"}  # Phase 1b: Orca and the web UI on the Mac (plan.md)
```

and in `check_scenarios`, replace

```python
        if meta.get("phase") not in {1, 2, 3, 4, 5, "backlog"}:
            problems.append(f"{name}: phase must be 1–5 or backlog")
```

with

```python
        if meta.get("phase") not in PHASES:
            problems.append(f"{name}: phase must be 1, 1b, 2–5 or backlog")
```

- [ ] **Step 4: See it pass** — the same command: 13 passed.
- [ ] **Step 5: The two scenario pages.** Create `website/scenarios/s24-pi-in-orca.md`:

```markdown
---
title: "S24 · pi in Orca on the Mac"
scenario-id: S24
phase: 1b
status: planned
---

**Situation.** At the Mac, I start pi from Orca, in one of its worktree panes, to work on code
with a Spark model. Claude Code runs in Orca beside it.

**What happens.** pi runs on the Mac, as me, and reaches llama-swap on the Spark over
`make tunnel`, as it does in a terminal. Claude Code started from Orca talks straight to
Anthropic, with no permission bypass. Agents started from Orca stop when the Mac does; long
unattended runs stay in tmux on the Spark (S12).

**What I see.** pi's footer names the Spark model that answers. Orca shows each agent as working,
waiting for input, or done.

**How to override.** None needed. Without the tunnel, pi's requests fail to connect: run
`make tunnel`. This scenario has no `spark doctor` check, since it runs on the Mac; doctor's
`Orca relay` line guards the Spark's side.
```

and `website/scenarios/s25-chat-from-the-mac.md`:

```markdown
---
title: "S25 · Chat from the Mac"
scenario-id: S25
phase: 1b
status: planned
---

**Situation.** At the Mac, with Tailscale on, I want to chat with the Spark's models.

**What happens.** Open WebUI, served over HTTPS through `tailscale serve` as on the phone (S09),
runs as a Safari web app from the Dock, with a login of its own, apart from Safari's.

**What I see.** The real model name on every reply. Voice input goes to speech-to-text.

**How to override.** Chrome's *Install page as app* if Safari's web app misbehaves. This scenario
has no `spark doctor` check, since it runs on the Mac.
```

- [ ] **Step 6: Check** — **on the Spark**:
  `uv run --frozen --project spark spark docs check-scenarios` passes, and `make test` passes
  (915 tests with Task 2's, 912 without).
- [ ] **Step 7: Commit** — **on the Spark**:

```bash
git add spark/src/spark/docs.py spark/tests/test_scenarios.py website/scenarios/s24-pi-in-orca.md \
  website/scenarios/s25-chat-from-the-mac.md
git commit -m "feat(spark): 🤖 the scenario check accepts phase 1b; S24 and S25" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

***

### Task 2 [Spark]: `make doctor` fails if Orca ever connected as Dan

**Files:**

- Modify: `spark/src/spark/doctor.py` (a constant after `ENGINE_CONFIG`; `_home` and `orca` after
  `spark_folders`; `orca(probe)` in `checks`)
- Modify: `spark/tests/test_doctor.py` (three new tests; three count assertions and one comment,
  15 → 16)
- Modify: `website/how-to/updates.md` (doctor's list of checks)

**Interfaces:** Consumes `Probe.exists(path) -> bool | None` (None: this account can't tell).
Produces `doctor.ORCA_RELAY = ".orca-remote"`, `doctor._home() -> Path`,
`doctor.orca(probe) -> Check` named `"Orca relay"`; `checks(...)` returns 16 results.

- [ ] **Step 1: Write the failing tests** — in `spark/tests/test_doctor.py`, change the check
  count from 15 to 16 in four places:
  - `test_a_healthy_spark_passes_every_check`:
    `assert len(results) == 16 and [c.name for c in results if not c.ok] == []`
  - `test_an_active_brake_whose_start_check_didnt_pass_fails_the_stack_units_line`: its comment
    becomes `# Folded into the stack units line, so doctor still counts 16 checks.` and its
    assertion `assert len(results) == 16 and set(failed) == {"stack units"}`
  - `test_the_report_shows_every_check_and_never_the_key`:
    `assert text.splitlines()[-1] == "doctor: 16 of 16 checks pass"`

  Then, just before `def test_needrestart_leaves_the_stack_alone():`, add:

```python
def test_orca_has_never_connected_as_you():
    # Phase 1b: brightroar, your own account, is never an Orca SSH target, so Orca's relay folder is never in your
    # home. A path doctor can't look at fails too, rather than passing as if it weren't there.
    relay = doctor._home() / doctor.ORCA_RELAY
    probe = FakeProbe()
    probe.present[relay] = True
    assert failures(probe) == {"Orca relay": f"{relay} exists: Orca has connected to the Spark as you, which it "
                                             "never should; website/how-to/orca.md's *Remove Orca* says how to "
                                             "take it off"}
    probe.present[relay] = None
    assert failures(probe) == {"Orca relay": f"can't tell whether {relay} exists: `ls -ld {relay}` says why"}


def test_the_orca_check_looks_in_a_real_home(tmp_path, monkeypatch):
    # The real probe, on a real folder: a planted relay folder fails the check, and its absence passes it.
    monkeypatch.setattr(doctor, "_home", lambda: tmp_path)
    (tmp_path / doctor.ORCA_RELAY).mkdir()
    assert not doctor.orca(Probe(ROOT)).ok
    (tmp_path / doctor.ORCA_RELAY).rmdir()
    assert doctor.orca(Probe(ROOT)).ok


def test_the_home_comes_from_the_password_database_not_home(tmp_path, monkeypatch):
    # $HOME can point anywhere, and "~" is never expanded inside a Path: the check must name a real, absolute folder.
    monkeypatch.setenv("HOME", str(tmp_path))
    home = doctor._home()
    assert home.is_absolute() and home != tmp_path and "~" not in str(home) and home.is_dir()


```

- [ ] **Step 2: See them fail** — **on the Spark**:
  `uv run --frozen --project spark pytest spark/tests/test_doctor.py -q`.
  Expected: 10 FAIL — the three new tests with `AttributeError: module 'spark.doctor' has no
  attribute '_home'`, and the seven count assertions with `15 == 16` (or `15 of 15`).
- [ ] **Step 3: Implement** — in `spark/src/spark/doctor.py`, after the
  `ENGINE_CONFIG = (Path("/etc/llama.cpp"), STATE_HOME / ".config")` line, add:

```python
# The folder Orca's SSH mode installs its relay in, in the home of the account it connects as. brightroar, your own
# account, is never an Orca target (plan.md, Phase 1b), so it must never be in your home.
ORCA_RELAY = ".orca-remote"
```

Right after `spark_folders`'s last line (its `return`), so that the two blank lines already there
come before the `# The stack` comment, add:

```python


def _home() -> Path:
    """This account's home, from the password database: not $HOME, which a shell or sudo can point anywhere."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def orca(probe: Probe) -> Check:
    """Orca never installed its SSH relay in your home (ORCA_RELAY). A path this account can't look at fails, rather
    than passing as if it weren't there."""
    relay = _home() / ORCA_RELAY
    there = probe.exists(relay)
    if there is None:
        return Check("Orca relay", False, f"can't tell whether {relay} exists: `ls -ld {relay}` says why")
    if there:
        return Check("Orca relay", False, f"{relay} exists: Orca has connected to the Spark as you, which it never "
                                          "should; website/how-to/orca.md's *Remove Orca* says how to take it off")
    return Check("Orca relay", True, f"{relay} doesn't exist: Orca has never connected as you")
```

And in `checks`, the second line of the list becomes:

```python
        firewall(probe), secrets_folder(probe), spark_folders(probe), orca(probe),
```

- [ ] **Step 4: See them pass** — the same command, then `make test`: 915 passed.
- [ ] **Step 5: `updates.md`** — in *After any update: is everything back?* (the paragraph after
  the `make doctor` block), the guardrails sentence ends "…and spark's folders as bootstrap sets them
  (`/var/lib/local-ai` root's, the brake's folder spark's, shared with `spark-admin`)." Add, before
  its full stop: `, and no Orca relay in your home (Orca never connects to the Spark as you)`.
  Rewrap the paragraph to 100 columns.
- [ ] **Step 6: Seen failing on the box** — **on the Spark**, as Dan, from the clone:

```bash
make status
mkdir ~/.orca-remote && make doctor
rmdir ~/.orca-remote && make doctor
```

Expected: the first doctor run prints `FAIL  Orca relay: /home/chendaniely/.orca-remote exists: …`
and ends `doctor: 15 of 16 checks pass`; the second ends `doctor: 16 of 16 checks pass`. Record
both lines for Task 6.
- [ ] **Step 7: Commit** — **on the Spark**:

```bash
git add spark/src/spark/doctor.py spark/tests/test_doctor.py website/how-to/updates.md
git commit -m "feat(spark): 🤖 doctor fails if Orca ever connected to the Spark as you" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

***

## ⇄ Switch point — Spark → Mac

- [ ] The Spark session runs *Before every push* with `phase-1b` as `<branch>`; then **Dan OKs**
  `git push`. **On the Mac**, in the worktree: `git pull`.

***

### Task 3 [Mac]: the Orca runbook, and the Mac in the web UI's and pi's runbooks

The Mac's, because these pages document and check the Mac's own clients (CLAUDE.md, *Where work
runs*).

**Files:**

- Create: `website/how-to/orca.md`
- Modify: `website/how-to/index.qmd`, `website/how-to/deploy.md` (*The web UI*),
  `website/how-to/pi.md` (*On the Mac*)

- [ ] **Step 1: Create `website/how-to/orca.md`** with exactly this text:

````markdown
---
title: "Orca on the Mac"
description: "Your Claude Code and pi in Orca's panes on the Mac, pi reaching the Spark over the tunnel: Orca's settings, its checks, and how to remove it."
---

[Orca](https://www.onorca.dev/docs) is a desktop app that runs command-line coding agents, each in
a pane with its own git worktree, a browser and a diff view. Here it stays **on the Mac, in its
local mode** ([the plan](../design/plan.md), Phase 1b): the agents it starts run on the Mac, as
you. pi started from it reaches the Spark's models over `make tunnel`, as pi in a terminal does,
and Claude Code started from it talks straight to Anthropic. Orca never connects to the Spark:
neither `brightroar` nor `brightroar-agent` is ever one of its SSH targets. Agents in Orca stop
when the Mac does; long unattended runs stay in tmux on the Spark
([S12](../scenarios/s12-long-agent-run.md)).

## Settings

In Orca's Settings:

- **Agent Permissions → Manual.** Orca's default gives every agent a flag that skips its
  permission prompts (`--dangerously-skip-permissions` for Claude). Manual gives none, so Claude
  asks and refuses as it does in a terminal.
- **Privacy → Share anonymous usage data: off.**
- **Agents → Agent status hooks: on.** They let Orca show whether each agent is working, waiting
  or done (see *What Orca changes on the Mac*).
- Leave each agent's own environment setting empty.

**On the Mac**, check them. This prints the telemetry setting, Claude's launch arguments, and the
names, never the values, of any environment variables Orca sets for Claude:

```bash
jq -r '.settings | "telemetry: \(.telemetry.optedIn)", "claude args: [\(.agentDefaultArgs.claude // "")]", "claude env: \(.agentDefaultEnv.claude // {} | keys)"' "$HOME/Library/Application Support/orca/profiles/local-default/orca-data.json"
```

Expected: `telemetry: false`, `claude args: []`, `claude env: []`.

## pi in Orca

**On the Mac**, from your clone of this repo, open the tunnel in a spare terminal, or in an Orca
terminal pane, and leave it open:

```bash
make tunnel
```

Then start pi from Orca, run `/model` and pick a Spark model: its footer names the model that
answers. It is the same pi as in a terminal, with the same `spark` provider
([pi, the coding agent](pi.md)). Without the tunnel, its requests fail to connect.

At home with Tailscale down, use the LAN alias instead. **On the Mac:**

```bash
SPARK_SSH_HOST=brightroar-lan make tunnel
```

Over WireGuard, from another tailnet, the same should work once
[S22](../scenarios/s22-another-tailnet.md) is set up; it is not yet tested.

## Claude Code in Orca

Orca runs your own `claude`, with your login; nothing between it and Anthropic changes. **On the
Mac**, with a Claude session started in Orca, check that no Claude process skips permissions. It
prints each Claude process's number and whether the flag is there, never its command line:

```bash
ps -axo pid=,comm= | awk '$2 ~ /(^|\/)claude$/ {print $1}' | while read -r p; do printf '%s skip-flag=%s\n' "$p" "$(ps -o args= -p "$p" | grep -c -- --dangerously-skip-permissions)"; done
```

Every line should end in `skip-flag=0`. Run it again after resuming a session in Orca: Orca starts
a resumed session another way.

## What Orca changes on the Mac

- **Your Claude Code settings.** Since 2026-09-24, one hook of Orca's on each of 13 events in
  `~/.claude/settings.json`, beside your own, rewritten at each Orca start. Outside Orca they print
  `{}` and exit; inside it they copy each event, prompts and tool inputs included, to Orca on
  127.0.0.1. A failed send of an event other than a tool call's is appended to a spool file for its
  pane, up to 5 MiB, emptied only after 7 days with no new failure. The hook scripts are in
  `~/.orca/agent-hooks/`.
- **pi.** Since 2026-09-28, three extensions in `~/.pi/agent/extensions/`: `orca-agent-status.ts`,
  `orca-prefill.ts` and `orca-titlebar-spinner.ts`. They load in every pi session, in Orca or not,
  and report to Orca only when Orca started pi.
- **Its own data**, in `~/Library/Application Support/orca/`: settings, scrollback and session
  history. A value that ever showed in a pane sits there too.
- **A command-line link**, `/usr/local/bin/orca`, root's.

The plan's *Claude is untouched* constraint allows these hooks only while they report and do
nothing else.

## After each Orca update

Orca releases about once a day, and an update can change its defaults. **On the Mac**, before
installing one, take a fingerprint of your Claude settings without Orca's hooks:

```bash
jq -S 'del(.hooks)' ~/.claude/settings.json | shasum -a 256
```

After installing it, **on the Mac**:

1. Run the check under *Settings*: expect the same three lines.
2. List the events Orca hooks. Expect the same 13; a new one needs a reason in Orca's release
   notes:

   ```bash
   jq -r '.hooks // {} | to_entries[] | select(any(.value[].hooks[]?; .command | test("orca"; "i"))) | .key' ~/.claude/settings.json
   ```

3. Check that the hook script still returns nothing but `{}`. Expect `0`:

   ```bash
   grep -c -E 'permissionDecision|hookSpecificOutput|additionalContext|systemMessage|"continue"|exit [1-9]' ~/.orca/agent-hooks/claude-hook.sh
   ```

4. Take the fingerprint again. A different result means Orca changed something outside `hooks`:
   look at what before starting Claude in Orca.
5. Start a Claude session in Orca, resume one, and run the process check under *Claude Code in
   Orca*.

If any answer is unexpected, turn Orca's status hooks off (Settings → Agents), and use Claude Code
in a terminal until it's understood.

## Never the Spark as a target

Orca offers every host in `~/.ssh/config` as an SSH target, `brightroar` and `brightroar-agent`
among them. Add neither:

- `brightroar` is your own account on the Spark, with sudo, the GitHub token and the Spark
  session's Claude settings. `make doctor` fails if Orca's relay folder, `~/.orca-remote`, ever
  appears in your home there.
- `brightroar-agent` would put Orca's relay, built from public npm, in `agent`'s home, and let Orca
  rewrite `agent`'s Claude settings, its secrets guard included. That route is in the plan's
  Backlog, with what has to come first.

If Orca ever did connect to the Spark, see *Remove Orca*.

## Remove Orca

To take Orca off the Mac, in this order:

1. In Orca, Settings → Agents → **Agent status hooks: off**. Orca removes its hooks from
   `~/.claude/settings.json`. **On the Mac**, check that they are gone; this prints nothing then:

   ```bash
   jq -r '.hooks // {} | to_entries[] | select(any(.value[].hooks[]?; .command | test("orca"; "i"))) | .key' ~/.claude/settings.json
   ```

2. Quit Orca, and move `/Applications/Orca.app` to the Trash.
3. **On the Mac**, remove what it left: its hook scripts, its data and its pi extensions. Your own
   pi extensions stay.

   ```bash
   rm -rf ~/.orca "$HOME/Library/Application Support/orca"
   find ~/.pi/agent/extensions -name 'orca-*.ts' -delete
   sudo rm /usr/local/bin/orca
   ```

If Orca ever connected to the Spark, also, **on the Spark**, as the account it connected as:
remove `~/.orca-remote` and `/tmp/.orca-relay-$(id -u)`, the hook entries in
`~/.claude/settings.json` whose command mentions Orca, and the `orca-*.ts` files in
`~/.pi/agent/extensions/`. Then `make doctor`, as you, passes its `Orca relay` line.

## What never goes in the repo

Orca shows details this public repo must never hold: the Spark's full tailnet name, in its SSH
target form and its host-key prompts; access links and QR codes; and, in screenshots, hostnames.
Its data files stay out too: `orca-data.json`, spool and scrollback files, and a relay's log.
````

- [ ] **Step 2: The how-to index** — in `website/how-to/index.qmd`, after the line that begins
  `Then ongoing, not once:`, add a blank line and:

```markdown
On the Mac, if you use it: [Orca on the Mac](orca.md) — your Claude Code and pi in Orca's panes.
```

- [ ] **Step 3: The web UI on the Mac** — in `website/how-to/deploy.md`, *The web UI*, after the
  paragraph that ends "…run the command above again.", add:

```markdown
On the Mac, the web UI can run as an app of its own: open the address in Safari, then **File → Add
to Dock**. The web app keeps a login of its own, apart from Safari's, so log in once inside it.
Chrome's **Install page as app** works too.
```

- [ ] **Step 4: pi in Orca** — in `website/how-to/pi.md`, *On the Mac*, after the paragraph
  "In pi, `/model` → a Spark model. The footer names the model that answers.", add:

```markdown
You can also start pi from Orca, in one of its panes: it's the same pi, with the same provider,
over the same tunnel ([Orca on the Mac](orca.md)). Orca adds three extensions of its own to
`~/.pi/agent/extensions/`, which load in every pi session, in Orca or not.
```

- [ ] **Step 5: Check** — **on the Mac**, from the worktree: run each command block in
  `orca.md`'s *Settings*, *Claude Code in Orca* and *After each Orca update* once (the fingerprint
  twice, with no Orca update between: the same result). None prints a value, a key or a command
  line. Then `make docs` renders the site, and `orca.md` appears in the how-to list.
- [ ] **Step 6: Commit** — **on the Mac**:

```bash
git add website/how-to/orca.md website/how-to/index.qmd website/how-to/deploy.md website/how-to/pi.md
git commit -m "docs(clients): 🤖 Orca on the Mac, and the web UI as a Mac app" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

***

### Task 4 [Dan]: S25, chat from the Mac

- [ ] **Step 1:** With Tailscale on, open the web UI's address in Safari, then **File → Add to
  Dock**. Open the new app from the Dock and log in.
- [ ] **Step 2:** Ask a question, and ask one by voice with the microphone (allow it when asked).
  Each reply names `gemma-4-26b-a4b`.
- [ ] **Step 3:** Give the Mac session the date; it goes on S25's page at Task 6.

***

### Task 5 [Dan + Mac]: S24, pi and Claude Code in Orca

Needs Task 0's settings. Dan does the Orca steps; the Mac session runs the checks, which print no
values.

- [ ] **Step 1 [Dan]:** **On the Mac**, `make tunnel` in a spare terminal. In Orca, start pi,
  `/model` → `qwen3.6-35b-a3b`, and ask it something small: the footer names the model. Close the
  tunnel (Ctrl-C) and ask again: the request fails to connect. Open the tunnel again.
- [ ] **Step 2 [Dan]:** In Orca, start a Claude agent, then resume an earlier Claude session in
  another pane. Leave both open.
- [ ] **Step 3 [Mac]:** Run `orca.md`'s process check (*Claude Code in Orca*): every line ends
  `skip-flag=0`. Run the *Settings* check: `telemetry: false`, `claude args: []`,
  `claude env: []`. Run the hook-events list: 13 events.
- [ ] **Step 4 [Mac]:** With Dan's OK, one read-only check that Orca never reached `agent`.
  **On the Mac:**

```bash
ssh brightroar-agent 'test -e ~/.orca-remote && echo PRESENT || echo absent'
```

Expected: `absent`.
- [ ] **Step 5:** Record the version for Task 6. **On the Mac:**

```bash
/Applications/Orca.app/Contents/Resources/bin/orca --version
```

(`/usr/local/bin/orca` can't find the app from its link on this Mac; the bundled CLI can.)

***

### Task 6 [Mac]: close Phase 1b

The Mac's, because it records the Mac's own state, renders the site and merges.

**Files:**

- Modify: `website/scenarios/s24-pi-in-orca.md`, `website/scenarios/s25-chat-from-the-mac.md`,
  `website/design/plan.md`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: Scenario statuses** — S24 and S25: `status: verified` and `verified: YYYY-MM-DD`
  (Tasks 5 and 4). Then **on the Mac**: `uv run --frozen --project spark spark docs check-scenarios`.
- [ ] **Step 2: README §Current state** — under *The MacBook — `heartsbane`*, add a bullet:
  `**Orca <version from Task 5>**, in its local mode (website/how-to/orca.md): installed
  2026-09-24; Agent Permissions Manual and telemetry off since <Task 0's date>; its status hooks on
  13 events in ~/.claude/settings.json since 2026-09-24, and its three pi extensions since
  2026-09-28. It has never connected to the Spark.` — with the link and code formatting of the
  bullets around it. Mention the web UI's Safari app in the same bullet list: `Open WebUI runs as a
  Safari web app (S25, <Task 4's date>).`
- [ ] **Step 3: The plan and the heads** — in `plan.md`, the Phase 1b entry gains a `*Status:*`
  line (done, with the date; what each done-when criterion rested on, Task 2 Step 6's two doctor
  lines included); a Revisions line records the close. `CLAUDE.md`'s and `README.md`'s opening
  paragraphs say Phase 1b is built and Phase 2 comes next.
- [ ] **Step 4: One reviewer** (Dan's decision: not a council) — a fresh reviewer reads the branch
  against the plan's Phase 1b entry and this plan: the Global Constraints, the Review Focus, the
  done-when. Fix what it finds, one commit per fix.
- [ ] **Step 5: Forward look** — anything Phase 2 must change? At least: harness hooks on the Mac
  beside Orca's, and session pins that see pi started from Orca (the plan's Phase 2 line). Record
  anything new in `plan.md` with a Revisions line.
- [ ] **Step 6: Commit** — **on the Mac**:

```bash
git add website/scenarios/s24-pi-in-orca.md website/scenarios/s25-chat-from-the-mac.md \
  website/design/plan.md README.md CLAUDE.md
git commit -m "docs(plan): 🤖 close Phase 1b: S24 and S25 verified" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Merge** — **on the Mac**, from the worktree, `make test lint docs` passes; then
  *Before every push* with `phase-1b` as `<branch>`, and **Dan OKs** `git push`. Then, in the main
  clone (`~/git/hub/local-ai`, whose own uncommitted work, if any, stays untouched):

```bash
git switch main && git pull
git merge --no-ff phase-1b -m "chore(repo): 🤖 merge phase 1b" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

git runs `pre-merge-commit` for a merge it completes by itself, which `.githooks` doesn't define,
so *Before every push* comes next, with `main` as `<branch>`. Then **Dan OKs**
`git push origin main`. **On the Spark**, Dan's clone moves back to `main`:
`git switch main && git pull`. The worktree goes once the merge is pushed: **on the Mac**, from
the main clone, `git worktree remove ../local-ai-phase-1b`, with Dan's OK.

***

## Done when

S24 and S25 are verified; a Claude that Orca starts, and one it resumes, carry no skip flag, and
telemetry is off; the doctor check, seen failing once, passes; the Spark is unchanged apart from
the repo's own code and docs (`agent`'s home has no `.orca-remote`, checked read-only as `agent`).
