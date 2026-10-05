---
title: "Orca on the Mac"
description: "Your Claude Code and pi in Orca's panes on the Mac, pi reaching the Spark over the tunnel: Orca's two settings, what it changes, and how to remove it."
---

[Orca](https://www.onorca.dev/docs) is a desktop app that runs command-line coding agents, each in
a pane with its own git worktree, a browser and a diff view. Here it stays **on the Mac, in its
local mode** ([the plan](../design/plan.md), *Orca*): the agents it starts run on the Mac, as you.
pi started from it reaches the Spark's models over `make tunnel`, as pi in a terminal does, and
Claude Code started from it talks straight to Anthropic. Agents in Orca stop when the Mac does;
long unattended runs stay in tmux on the Spark ([S12](../scenarios/s12-long-agent-run.md)).

## Settings

In Orca's Settings:

- **Agent Permissions → Manual.** Orca's default gives every agent a flag that skips its
  permission prompts (`--dangerously-skip-permissions` for Claude). Manual gives none, so Claude
  asks and refuses as it does in a terminal.
- **Privacy → Share anonymous usage data: off.**
- **Agents → Agent status hooks: on.** They let Orca show whether each agent is working, waiting
  or done.
- Leave each agent's own environment setting empty.

**On the Mac**, check them. This reads Orca's settings database without changing it, and prints
the telemetry setting, Claude's launch arguments, and the names, never the values, of any
environment variables Orca sets for Claude:

```bash
sqlite3 -readonly "$HOME/Library/Application Support/orca/profiles/local-default/profile-state.db" "select payload from profile_state_documents where domain='settings';" | jq -r '"telemetry: \(.telemetry.optedIn)", "claude args: [\(.agentDefaultArgs.claude // "")]", "claude env: \(.agentDefaultEnv.claude // {} | keys)"'
```

Expected: `telemetry: false`, `claude args: []`, `claude env: []`. *(Corrected 2026-10-05: this
read `orca-data.json`, beside the database. That file is an export Orca writes now and then, so it
still showed the old settings after they were changed.)*

## pi in Orca

**On the Mac**, from your clone of this repo, open the tunnel in a spare terminal, or in an Orca
terminal pane, and leave it open:

```bash
make tunnel
```

Then start pi from Orca, run `/model` and pick a Spark model: its footer names the model that
answers. It is the same pi as in a terminal, with the same `spark` provider
([pi, the coding agent](pi.md)); without the tunnel, its requests fail to connect. At home with
Tailscale down, `SPARK_SSH_HOST=brightroar-lan make tunnel` uses the LAN alias instead.

## Claude Code in Orca

Orca runs your own `claude`, with your login; nothing between it and Anthropic changes. **On the
Mac**, with a Claude session open in Orca, check that no Claude process skips permissions. It
prints each Claude process's number and whether the flag is there, never its command line:

```bash
ps -axo pid=,comm= | awk '$2 ~ /(^|\/)claude$/ {print $1}' | while read -r p; do printf '%s skip-flag=%s\n' "$p" "$(ps -o args= -p "$p" | grep -c -- --dangerously-skip-permissions)"; done
```

Every line should end in `skip-flag=0`, for a resumed session as well as a new one.

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
- **Its own data**, in `~/Library/Application Support/orca/`: settings (in
  `profiles/local-default/profile-state.db`, with `orca-data.json` an occasional export of them),
  scrollback and session history. A value that ever showed in a pane sits there too.
- **A command-line link**, `/usr/local/bin/orca`, root's.

The plan's *Claude is untouched* constraint allows these hooks only while they report and do
nothing else. Orca releases about once a day, and an update can put its defaults back: after one,
run the check under *Settings* again. **On the Mac**, this lists the events Orca hooks (expect the
same 13):

```bash
jq -r '.hooks // {} | to_entries[] | select(any(.value[].hooks[]?; .command | test("orca"; "i"))) | .key' ~/.claude/settings.json
```

## Never the Spark as a target

Orca imports every host in `~/.ssh/config` as an SSH target by itself — on this Mac it did on
2026-09-29, the Spark's aliases among them — so they sit in its list without being added. Connect
to none of the Spark's, and delete them from the list so a click can't. `brightroar` is your own
account there, with sudo and the GitHub token; `brightroar-agent` would let Orca build its relay
from public npm in `agent`'s home and rewrite `agent`'s Claude settings, its secrets guard
included. That route is in the plan's Backlog, with what has to come first. *(Corrected
2026-10-05: this said Orca "offers" the hosts, as if a target had to be added before it was
listed.)*

**On the Spark**, as either account, this shows whether Orca ever connected as it: Orca's relay
folder is there only if it did.

```bash
test -e ~/.orca-remote && echo "Orca has connected as this account" || echo "never connected as this account"
```

## Remove Orca

1. In Orca, Settings → Agents → **Agent status hooks: off**. Orca removes its hooks from
   `~/.claude/settings.json`; the hook-events check above then prints nothing.
2. Quit Orca, and move `/Applications/Orca.app` to the Trash.
3. **On the Mac**, remove what it left: its hook scripts, its data and its pi extensions. Your own
   pi extensions stay.

   ```bash
   rm -rf ~/.orca "$HOME/Library/Application Support/orca"
   find ~/.pi/agent/extensions -name 'orca-*.ts' -delete
   sudo rm /usr/local/bin/orca
   ```

## What never goes in the repo

Orca shows details this public repo must never hold: the Spark's full tailnet name, in its SSH
target form and its host-key prompts; access links and QR codes; and, in screenshots, hostnames.
Its data files stay out too: `profile-state.db`, `orca-data.json`, spool and scrollback files.
