# local-ai

Personal local-AI stack for a **single DGX Spark (GB10)**, reached from a MacBook or other devices
over Tailscale (the primary path), the home LAN, or WireGuard (for a device on another tailnet).
Design settled on 2026-09-23. Phase 0 is built: the leak-guard hooks and CI, the `spark` CLI's leak
check and docs tools, the host bootstrap (applied to the box on 2026-09-24), and the docs site with
its runbooks and scenario pages. Nothing serves a model yet; that starts with Phase 1.

**The machine is a GIGABYTE AI TOP ATOM** (`ATAGB10-9002` rev 1.0), hostname `brightroar` — an OEM
DGX Spark variant, **not** NVIDIA's Founders Edition. In this repo "the Spark" always means this
box. GB10 silicon is identical, so all the GB10 material applies; anything vendor-specific
(recovery media, firmware, support) comes from GIGABYTE, not NVIDIA. Full spec:
[`README.md`](README.md#hardware).

**Read [the plan](website/design/plan.md) before proposing any architecture.** It holds the goals,
the constraints, the decisions with Dan's reasoning, the design, the phases, and the open items and
risks; its Revisions list records every change to it. It replaced `planning.md`, the initial plan,
on 2026-09-23 — the original is in git history (`git show f62d2c7:planning.md`).
[`README.md`](README.md#current-state) §Current state records how the machines actually stand,
[`cosmicbboy-local-ai.md`](cosmicbboy-local-ai.md) holds the GB10 hardware facts, and
[`changelog.md`](changelog.md) logs what changed on the machine and when.

## Non-negotiable constraints

- **Claude Code and Claude Desktop stay untouched.** No gateway, proxy, `ANTHROPIC_BASE_URL`, or
  globally-installed MCP servers anywhere in the Claude path. The Spark is a *separate* mode for
  coding harnesses (pi, OpenCode), my own pipelines and apps, and a web UI. Violating this defeats
  the repo's primary goal.
- **One Spark, not two.** The reference repo is a 2-node cluster. Do **not** apply its RoCE, NCCL,
  Ray, or `TP=2` material here — none of it can affect a single node. `cosmicbboy-local-ai.md` §H
  lists exactly what's excluded and why.
- **Secrets by reference only** — `${VAR}` expanded at call time, values in `~/.secrets`, never
  committed or printed. Every config file added here must stay secret-free — see the publishing rule below.

## ⛔ Public repo — history is permanent

`github.com/chendaniely/local-ai` is **public**. Anything committed is world-readable from the
moment it is pushed, and **a later commit that deletes it does not remove it**. It remains in the
history, in every clone and fork, and in GitHub's dangling objects, which stay reachable through
the API long after no branch references them.

**So the check happens before `git add`, never after.** A follow-up "remove secret" commit is not
a fix and must never be treated as one.

Never write into this repo:

- **Credentials of any kind** — keys, tokens, passwords. Names only (`${VLLM_API_KEY}`), never
  values.
- **Tailnet IPs (`100.x`) or full LAN addresses.** A bare last octet (`.200`) is fine where the
  subnet is not also stated.
- **MAC addresses, serial numbers, or other per-unit identifiers.**
- **Personal hostnames or DDNS names that resolve from outside**, including the Synology and
  YouTrack ones.
- **Unread terminal paste.** `env`, `ip addr`, `tailscale status`, `docker inspect` and
  `nvidia-smi` output all carry identifiers. Read it before it goes in.

Internal hostnames (`brightroar`, `heartsbane`) and the GB10 hardware facts are deliberate
exceptions — they are documented because the repo is useless without them.

**Where that material goes instead.** Banning it here does not make it unnecessary — the MACs, the
full addresses, the serial and the purchase record still have to be written down somewhere. They
live in the Obsidian vault, in `zettelkasten/local-ai/`. The entry note is
`brightroar Local AI Stack.md`, which records the split explicitly — what belongs in this repo
versus what belongs there — and holds the slots for the full factory hostname, both NIC MACs, the
full LAN and tailnet addresses, the serial/service tag, the purchase record, and the accounts
created during first-time setup. That vault has no git remote.

Phase 0 added a few more private files, all outside the repo: one secret file per service on the
Spark, the denylist the leak-check hooks read (one on each machine), and the Tailscale ACL policy.
A private values file holding the NAS and LAN addresses the configs need is still to come. The
vault's entry note records each one when it is created.

⚠️ **Somewhere else is not permission to write values down.** Credentials stay by reference in the
vault too, exactly as in *Non-negotiable constraints* above — which secret exists, where its value
lives, and the variable it is referenced as; never the value, never a masked prefix. The vault
syncs to a NAS and across several machines, so a leaked vault must not be a leaked credential.

**If something sensitive does get pushed:** rotate the credential *first* — that is the only step
that actually closes the exposure — then rewrite history with `git filter-repo` and force-push.
Treat the rewrite as cleanup, not remediation; assume the value is already compromised.

## ⛔ Docs must be true

This repo is **almost entirely documentation** — right now the docs *are* the product, not a
description of one. That makes a wrong doc worse than a missing one: it gets believed, acted on,
and copied into the next decision before anyone checks it. The damage is never local, because
every file here feeds the next.

**Fix the doc in the same change that makes it wrong** — not "later", not in a follow-up commit.
If a change makes a sentence untrue, correcting that sentence is part of the change, and the
change is not done until it is.

These files are not independent. Known sync obligations:

| When this changes | This must change with it |
|---|---|
| Anything about the box — hostname, network, installs, OS | `changelog.md` gains the dated entry **and** `README.md` §Current state gains the new *current* state |
| An open item gets settled | the plan's Requirements or Design gain the decision, its "Open items and risks" entry is marked resolved with the date, **and** a Revisions line records it |
| A hardware fact is corrected | `README.md` §Hardware, plus any number in the plan derived from it |
| The stack's behaviour changes | its page under `website/scenarios/` **and** its `spark doctor` check, in the same commit |
| Something learned affects a later phase | the plan and its Revisions, the affected scenario pages, **and** `changelog.md` if the box changed |
| A claim gets measured on this box | `cosmicbboy-local-ai.md` `[adapted]` → `[verified]` — never without the actual measurement |
| A rule changes | This file, **and** the `README.md` §Conventions summary of it |
| Where private material lives | This file, `README.md` §My environment, and the vault's own entry note |

**Correct, don't delete.** Superseded material gets annotated with what replaced it and when. A
dated correction is information — it records what was believed and why it was wrong. A silent
deletion destroys that and invites the same mistake again.

**Keep unverified things marked unverified.** `README.md`'s factory-reset procedure is transcribed
from NVIDIA's docs and flagged *not yet performed on this box*; the GIGABYTE route is flagged
*reported but not verified*. Those markers are load-bearing. Never quietly upgrade one — promote it
only when something was actually done or measured, same as `[adapted]` → `[verified]`.

## GB10 gotchas that cause wrong work

- **`free -g`, never `nvidia-smi`** — the GPU shares the CPU's LPDDR5X pool and `nvidia-smi`
  reports `[N/A]` for memory.
- **~121 GiB unified memory total**, ~105–110 GiB usable for weights + KV cache. Models over
  ~110 GB do not fit at all. 128 GB is soldered — it is a permanent ceiling, not an upgrade path.
  The plan budgets against the CUDA-allocatable ceiling instead (reported near 102 GiB; to be
  measured) and keeps ≥24 GiB free on admission.
- **Only 1 TB of NVMe**, and weights, the HF cache and NGC container images all share it. That is
  single-digit large models on disk. Don't plan a model zoo; the plan keeps weights local and puts
  cold storage on the Synology in its backlog.
- **One memory pool — nothing loads unless it fits.** Decided 2026-09-23: llama-swap supervises the
  engines and `spark-gate` admits a load only if it fits in free memory; it never evicts, never
  substitutes, and every refusal explains itself (the plan, *Admission and memory rules*). Don't
  design anything that loads models around the gate.
- **Overcommitting memory can hard-freeze the box** — no OOM kill, just a power cycle (reported;
  open NVIDIA driver issue #1358). The admission reserve and the brake exist for this; don't loosen
  them casually.
- **Never reload llama-swap while models are loaded** — in v257 a config reload stops every engine.
  Changes go through `spark apply`, which waits for idle or asks.
- **Tens of tok/s is the realistic band** on a large MoE. Don't promise more: the reference repo's
  75 tok/s needed *two* Sparks **and** speculative decoding.
- **`sm_121`** — from-source builds need `CMAKE_CUDA_ARCHITECTURES=121` and
  `TORCH_CUDA_ARCH_LIST=12.1a`, or they silently target the wrong arch. NVIDIA's own llama.cpp
  playbook uses `121a-real`; which is right gets verified in Phase 1.
- **The GPU set moves as one, and only on upgrade day.** The kernel, the NVIDIA modules built for
  it, the driver and CUDA are held together (`apt-mark showhold` lists them). Never unhold or
  upgrade part of the set, while debugging or otherwise: a kernel with no matching NVIDIA module
  boots without a GPU. It moves on upgrade day, as one, following `website/how-to/updates.md`, and
  `make hold-gpu` holds it again.

## Conventions

- `cosmicbboy-local-ai.md` labels every claim **`[verified]`** (measured on real GB10 hardware by
  Niels) or **`[adapted]`** (my untested translation to one node). Preserve these labels. Never
  promote `[adapted]` → `[verified]` without an actual measurement on this box.
- Reference repo: <https://github.com/cosmicbboy/local-ai> — Niels Bantilan's 2× Spark stack, read
  at commit `d32fab0`.
- Work here is self-contained (no external stakeholder, no deadline), so it tracks locally in the
  plan (`website/design/plan.md`) — not YouTrack.
- **Markdown under `website/`: a mid-document horizontal rule is `***`, never `---`.** Pandoc can
  read a `---` line anywhere in a document, not only at the top, as the start of a YAML metadata
  block, and then the Quarto render fails. The front matter's own `---` lines are fine.

## Building it

- **Work runs where it belongs.** On `heartsbane` (the Mac): code with unit tests, config templates
  and render tests, the Makefile, leak hooks, CI, the docs site, Mac clients. On `brightroar` (a
  Claude Code session as Dan, in tmux): anything touching the GPU, memory, systemd or Docker,
  including `spark doctor`. Dan: sudo, interactive logins, secret values, the Synology's settings.
  Every task in an implementation plan is labelled **[Mac]**, **[Spark]** or **[Dan]**; from the Mac,
  touch the Spark only with read-only SSH checks Dan has OK'd.
- **One session at a time.** The active session owns the phase branch; at a switch it commits, the
  branch is pushed with Dan's OK, and the other machine pulls.
- **Commit along the way.** A checkpoint commit after each task, on a branch per phase; a phase
  merges to `main` after its council review and Dan's OK. **Push only with Dan's explicit OK.**
- **Check work often, then look forward.** Each task ends with its tests plus a check against the
  plan and its scenarios; each phase ends with a council review. If anything learned affects a later
  step, update the plan (and its Revisions) before continuing.
- **Python through uv, the `Makefile` as the front door.** No system Python, no pip; the Makefile
  calls `uv run --frozen spark …`; standalone scripts carry PEP 723 inline metadata. One Python
  minor version everywhere, pinned in `spark/.python-version`: it has to live in `spark/`, because
  uv looks for it only in the project directory.
- **The `agent` user never gets Dan's credentials** — no sudo, no docker group, no GitHub token, no
  access to Dan's home or `~/.secrets`. It holds only credentials of its own: its Claude Code login
  and, from Phase 1, its own llama-swap key. (Corrected 2026-09-25: this said `agent` never gets
  credentials at all, which stopped being true when it got its own Claude Code login.)
- **Never bypass the leak hooks** — no `git commit --no-verify` in this repo once `.githooks` exists
  (Phase 0).

### Lessons from Phase 0 — rules that prevent rework

Each of these cost Phase 0 at least one review loop; the story is in
[`website/design/phase-0-retro.md`](website/design/phase-0-retro.md).

- **Run every code block before it goes into a plan**, and keep the listing byte-identical to the
  tested code — implementers copy plans faithfully, bugs included.
- **Test shell, Makefile, `ps` and apt/dpkg behaviour on the Mac (bash 3.2, GNU make 3.81) and on
  Ubuntu 24.04 (bash 5.2, GNU make 4.3, procps-ng 4)** — a throwaway `ubuntu:24.04` container
  works; `make -n -C`, `ps -o oom_score_adj` and dpkg's hold letter each behaved differently there.
- **See every test and check fail once, and make fakes change state as the real tool does** — a
  check against a file that didn't exist, tests that matched no line, and a fake `apt-mark` that
  never set the hold letter all passed while broken.
- **Never assert a box fact from the Mac** — mark it unverified and let the Spark session check it;
  the login name, `~/.secrets` and DGX OS's package names were all wrong guesses.
- **Root never writes, `chown`s or `chmod`s through a path `spark` or `agent` controls** — `agent`'s
  files are written by `agent` (`sudo -u`, `runuser -u`), and temporary files go in `mktemp -d`,
  never a fixed `/tmp` name; a planted symlink turns root's write into theirs.
- **No secret on a command line, and no check that can print one** — values travel through `printf`
  (a builtin) or a file, and a check prints only a verdict, even in the case it exists to catch;
  argv shows in the process list.
- **No `sudo` inside a piped `ssh`** — it has no terminal to ask for the password; `scp` the file,
  then run `sudo` in an interactive session.
- **A held dpkg package reads `hi`, not `ii`** — count both as installed, and compare package
  states without the hold letter, or a held set looks empty and every comparison differs.
- **A GRUB check reads what `grub.cfg` will boot** — entry 0's `linux` line, the `default=` lines
  and `grub-editenv list` — not only `/etc/default/grub`, whose settings miss `GRUB_TOP_LEVEL`,
  `GRUB_FLAVOUR_ORDER` and indented or exported lines. No GRUB id goes in the repo: it carries the
  root filesystem's UUID.
- **Review permissions, secrets and network exposure in the task that changes them** — Phase 0's
  security lens came at the phase-end council, after bootstrap had run, and left box steps pending.
- **A step comes after everything it uses** — tools, aliases, the tailnet, the session that runs
  it; the Spark session was first scheduled after the task that ran in it.
- **Re-read every sentence a fix touches against the code before committing** — a fix's review
  usually found wording the fix itself had made untrue.
- **Write plans, runbooks and commit messages with file tools, and commit with
  `git commit -F <file>`** — the secrets hook refuses shell commands that name a secret path, and
  shell expansion garbled one commit message.
- **A subagent's commit carries the exact trailer and is never amended without Dan's OK** — one went
  in with another model's trailer.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`.

Types in use here: `docs`, `chore`, `feat`, `fix`, `refactor`, `build`, `ci`. Scope is the area
touched — `plan`, `readme`, `changelog`, `machine`, `repo`, and, as code arrives, `stack`, `spark`,
`clients`, `website`. (`planning` was the scope for the retired `planning.md`.)

**A commit written by an AI/LLM carries a 🤖 immediately after the `type(scope):` prefix:**

```
docs(planning): 🤖 record wired network state and DHCP reservations
chore(repo): 🤖 harden gitignore for public repo
```

Human-written commits use the same format without the 🤖. The marker describes who wrote the
commit, so it applies to anything the model authored — including work a human asked for.

## Environment

- The Bash tool's cwd resets between calls; use absolute paths or `git -C ~/git/hub/local-ai`.
