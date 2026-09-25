---
title: "Phase 0 — retrospective"
description: "What Phase 0 built, how it ran, where it departed from the plan, what the reviews found, why it took so many loops, what Phase 1 inherits, and how to start over."
date: 2026-09-25
---

# Phase 0 — retrospective

Phase 0 ran from 2026-09-23 to 2026-09-25: [its implementation plan](phase-0.md), executed task by
task on branch `phase-0`, then reviewed and fixed until no review found anything important. That
took 68 commits before this page, `89cad0c..60e0d30`. [The plan](plan.md) stays the place for
decisions. This page is the history behind them, written so that a rebuilt box, or the next phase,
takes fewer loops.

## What Phase 0 delivered

| Piece | What it is | Where |
|---|---|---|
| Leak guards | pre-commit and commit-msg hooks: gitleaks, then the repo's own patterns and a private denylist, over every commit's files, file names and message. `make hooks` turns them on in a clone, after checking that gitleaks has its `git` command and that the denylist has terms. | [Leak guards](../how-to/leak-guards.md) · `.githooks/` |
| CI | Four jobs: the tests; the leak scans (gitleaks over the whole history, and the repo's patterns over every tracked file and every commit's patches and messages); shellcheck; the site build. Publishing the site is a manual workflow. Dependabot proposes GitHub Actions and `spark/uv.lock` updates every Friday. | `.github/` |
| The `spark` CLI | A uv project on Python 3.12, pinned in `spark/.python-version`: `spark leakcheck`, `spark docs stack` and `spark docs check-scenarios`, with 109 tests. | `spark/` |
| Pinned versions | `stack/versions.yaml`, rendered into the Stack page. | [Stack](../reference/stack.md) · `stack/versions.yaml` |
| Host bootstrap | `make bootstrap`: packages, the GPU-set hold, the users `spark` and `agent`, directories, a headless boot, earlyoom, ufw with SSH only, the polkit rule. `make hold-gpu` re-holds the GPU set and nothing else. Each has a `-dry-run` target. | [Bootstrap](../how-to/bootstrap.md) · `stack/host/` |
| Runbooks | Seven, in the order the How-to page lists them: the Spark session, leak guards, bootstrap, SSH from the Mac, Tailscale and secret files, then updates, which is ongoing. | [How-to](../how-to/index.qmd) |
| Scenarios | S01–S23, all *planned*. S23, upgrade day, was added in Phase 0. | [Scenarios](../scenarios/index.qmd) |
| The box | Bootstrapped on 2026-09-24: headless, the GPU set held (151 packages), on the tailnet as `tag:spark`, and the Phase 1 secret files in place. | `README.md` §Current state · `changelog.md` |
| Phase 1's plan, revised | Phase 0's lessons carried forward: a new Task 10 (a needrestart override, `make upgrade-gpu`, `make doctor` v0), the Spark session set up before any **[Spark]** task, and two box steps gated before anything faces the network. | [Phase 1](phase-1.md) |

Every "Phase 0 is done when" criterion holds. The headless `free -g` baseline is recorded. The
planted private address is refused on both machines. `agent` can't read Dan's files or use Docker,
and `nvidia-smi` run as `agent` names the GPU; a real CUDA program as `agent` waits for Phase 1's
first engine. The site builds. CI passed on the branch on 2026-09-24; Task 12's commits get their
run at the next push, and `main` gets its run at the merge.

## How it ran

| When | Where | What happened |
|---|---|---|
| 2026-09-23 | — | The plan and this phase's implementation plan were written the day the Spark arrived, and `phase-0` was cut from `main` at `89cad0c`. |
| 2026-09-23 to 24 | Mac | Tasks 1–8 (`89cad0c..80b27d1`), subagent-driven: an implementer per task, then a spec and quality review. One task needed a fix round. The hooks stayed off, because the denylist didn't exist yet, so each task's diff was scanned by hand. |
| 2026-09-24 | Mac | A whole-branch review: 1 Critical (scan the branch's history and messages with the denylist before anything is public), 5 Important, 16 Minor. One fix wave (`80b27d1..1adb1e0`) was re-reviewed, and only minors were left. Then the push gate: Dan's denylist, `make hooks`, the leak drill, the scans of the tree, the history and the messages, and Dan's OK to push. |
| 2026-09-24 | Spark | Tasks 9–11 (`1adb1e0..6893c1d`), in a Claude Code session on the box and by Dan. The dry run and the inventory found the login and the hold wrong, and Dan made two decisions about updates. Dan bootstrapped the box, joined Tailscale and wrote the secret files; the session checked and recorded it all. SSH from the Mac got its own runbook (`ed348f5`, `96d0217`). |
| 2026-09-24 to 25 | Mac | Task 12: the vault's entry note, then a council of four reviewers (goal-fit and scenarios, reliability, security and simplicity, toolstack). Each said "with fixes"; none found anything Critical. Three fix waves followed, each reviewed. G1, the code (`96d0217..fe3d8b7`), passed its first review. G2, the as-built docs (`fe3d8b7..f1c30f2`), needed one fix round. G3, the forward look into Phase 1's plan (`f1c30f2..0eb3760`), needed three. |
| 2026-09-25 | Mac | A polish pass for the first upgrade day, 2026-09-26, mostly the check of which kernel GRUB boots (`0eb3760..60e0d30`, reviewed three times). Then this page, before the merge. |

Of the 68 commits, 45 answered reviews: 7 after the whole-branch review, and 38 in Task 12. The
ranges are git's `A..B`: the commits after `A`, up to and including `B`.

## Where the build departed from the plan, and why

| What changed | Why | Commits |
|---|---|---|
| Dan's login on the Spark isn't `dan`, the Mac's login. Bootstrap takes its admin from whoever runs it. | The plan assumed it, and the runbooks hardcoded `/home/dan`. Under the wrong name, one check always passed and one step deleted the key it had failed to copy. | `3cf93e4` |
| The GPU set is held as one: the kernel metapackages, the NVIDIA modules, the driver, and CUDA with its version-named libraries. | The plan held `nvidia-*`, `libnvidia-*` and `cuda-*` only, which left the kernel and its modules free to move; DGX OS ships all four together. Dan's decision. | `cb0ec0b`, `f77a144` |
| Everyday `apt upgrade` runs any time. The GPU set moves only on upgrade day, weekly on Saturdays. | Dan runs apt out of habit, so the held set needs a written plan for when it moves. Weekly rather than monthly was Dan's call. | `cb0ec0b`, `8bb80e1` |
| There is no tailnet route home. | Every device Dan uses runs Tailscale, and the Spark sits on the LAN. Dan's decision. | `f4390df`, `13633aa` |
| The secrets runbook is `secret-files.md`. | `.gitignore`'s `secrets.*` rule, kept as strict as it was, ignores a file named `secrets.md`. Dan's decision. | `80b27d1` |
| `/var/lib/local-ai` is root's (`root:root 0755`), and bootstrap never acts inside `agent`'s home. | A re-run as root followed any symlink `spark` or `agent` could plant there, which is a path to root. | `3735420` |
| The hooks came on only once Dan's denylist existed. | Without it they refuse every commit. Tasks 1–8 and their fix wave were committed with the hooks off and scanned by hand, then scanned again with the denylist, tree, history and messages, before Dan's OK to push. | the push gate, 2026-09-24 |
| `make hold-gpu` re-holds the GPU set and nothing else. | Upgrade day re-ran all of bootstrap just to re-hold, which stops a running desktop, restarts earlyoom and resets owners and modes. | `f77a144`, `ed0e06a` |
| Bootstrap installs every apt package the box relies on. | A rebuild must not depend on what DGX OS happens to ship. | `d0c4d3c` |
| gitleaks on the Spark is the 8.30.1 release binary in `/usr/local/bin`. | Ubuntu's archive has 8.16, which lacks `gitleaks git`, and there is no snap. | `0c86fae` |
| A mid-document rule under `website/` is `***`. | Pandoc read `---` as the start of a YAML block, and the render failed. | `cbb28b2` |
| Dependabot proposes GitHub Actions and `spark/uv.lock` updates. | The plan's "automated PRs" had been dropped without a word. What Dependabot can't read went to the Backlog. | `c0653ca` |
| One Python minor version, 3.12, pinned in `spark/.python-version`. | Nothing pinned it, and the Mac's had floated to 3.14; uv looks for the pin only in the project directory. | `fe3d8b7` |
| The Spark session is set up first, with the Mac's rules and secrets guard, and its GitHub token can push to this repository only. | Its setup was scheduled after the task that ran in it, and a plain `gh auth login` can push to every repository Dan can. | `8bc5a3b`, `0c976e6`, `a4da76f` |
| `make doctor` v0 moves into Phase 1. | Weekly upgrade day starts on 2026-09-26 and needs a check after it. `spark doctor` proper stays in Phase 2. | `e503da1` |
| The plan's code listings stay as written, marked *Superseded* where the code moved on. | Correct, don't delete. The code in the repo is the as-built version. | `13633aa` |

## What the reviews found, by theme

| Theme | What was found | Fixed in |
|---|---|---|
| Leak-check gaps | A private address at the end of a sentence passed, and so did every IPv6 private or tailnet address. The allow marker excused denylisted terms too, and a bad denylist line gave a traceback. Type changes, UTF-16 text, file names and an empty denylist passed, and binaries were skipped without a word. `make hooks` accepted a gitleaks too old for the hooks, and a missing `uv` gave a bare error. In CI the repo's patterns read only the tree, never earlier patches or commit messages. `*.env` files weren't ignored. | `8e0a5e7`, `cbd7ca3`, `f581016`, `783a619`, `bad1b55`, `05921a1` |
| Root writing where `spark` or `agent` can plant a symlink | Bootstrap re-applied owners inside `spark`'s `/var/lib/local-ai` and in `agent`'s home. The runbook wrote `agent`'s `authorized_keys` as root. Phase 1's plan wrote `agent`'s key file as root, and ran Node's installer from a fixed `/tmp` name. | `3735420`, `16094cc`, `e503da1` |
| The GPU hold failing silently | The hold missed the kernel and the NVIDIA modules. Held packages read as `hi` and dropped out of the set, half-configured packages were skipped, and nothing checked that the hold took. | `cb0ec0b`, `f77a144`, `ed0e06a` |
| Upgrade day booting a kernel with no NVIDIA module | A stopped upgrade left the set released. Nothing checked the new kernel's module before the reboot, and the recovery line could neither recover nor be run twice. apt could swap the driver branch. The check of which kernel GRUB boots read only GRUB's settings, and so missed `GRUB_TOP_LEVEL`, `GRUB_FLAVOUR_ORDER` and indented or exported lines; it now reads `grub.cfg` and `grub-editenv`. In Phase 1's plan, early versions of `make upgrade-gpu` compared the set including its hold letter, and could suggest a restart after a new kernel arrived without its module. | `1e1abbc`, `833666a`, `9e36459`, `ad6c896`, `8476142`, `0d35bfb`, `3345b19`, `3979f5a`, `0eb3760` |
| CI fragility | One step downloaded, checked and ran gitleaks, so a network blip read as a leak. The job token stayed in the checkouts, and no job had a time limit or retried a download. Nothing proposed updates, so two Actions pins fell behind unnoticed. | `bad1b55`, `ed0e06a`, `c0653ca` |
| Runbook commands that could print a secret, or couldn't work | A check `cat`ed Dan's secrets file in exactly the case it exists to catch. A key that a later step copies was never created, so three keys would have been written empty. `sudo` inside a piped `ssh` had no terminal to ask for the password. A failed checksum didn't stop an install. A check meant to list names could print a value. | `41da7fb`, `2381c21` |
| Access wider than needed | The Spark's `gh` login could push to every repository Dan can, and the Spark session's runbook didn't carry over the Mac's secrets rule and guard. The SSH runbook had no keys-only step and no public IPv6 check. | `8bc5a3b`, `0dc7a3a` |
| Docs that had become untrue | The plan's route home, the secret files' mode, who turns the hooks on and where uv's Pythons live; `CLAUDE.md`'s "almost no code yet"; the README's contents. Phase 1's plan lacked three things this plan promised for Phase 1. `CLAUDE.md` said `agent` never gets credentials. The plan said screenshots and Actions logs get checked, and neither is read. The leak-guards runbook lagged the hooks' new checks, and read every red leaks step in CI as a finding. | `13633aa`, `15e81fc`, `e503da1`, `f1c30f2`, `2d030e5`, `5debe62`, `d750eab` |

## Why there were so many loops

Phase 0 is small: a CLI, two hooks, one shell script and a set of docs. Yet Task 12 alone took three
fix waves and a polish pass, and six more rounds to fix what their reviews found. The causes, each
with the rule it now has in `CLAUDE.md` (*Lessons from Phase 0*):

1. **Plan code that was never run.** Phase 0's code listings went into the plan untested. The
   implementers copied them faithfully, so the plan's bugs arrived as code: a check that could print
   the secrets file, `sudo` inside a piped `ssh`, a key that was never created, tests that passed
   with nothing to check, and hold patterns that missed half the set. *Rule:* run every code block
   before it goes into a plan, and keep the listing byte-identical to the tested code. G3 worked
   this way for Phase 1's new code.
2. **Testing only on the Mac.** Even prototyped code broke on the box's userland. GNU make 4.3
   prints `Entering directory` lines under `make -n -C`. procps-ng 4's `ps` rejects `oom_score_adj`.
   dpkg marks a held package `hi`, which the tests' fakes never did, so a before-and-after
   comparison that passed on the Mac would always have differed on the box. A throwaway
   `ubuntu:24.04` container reproduced each one. *Rule:* test shell, Makefile, `ps` and apt/dpkg
   behaviour on both, and make fakes change state the way the real tool does.
3. **Box facts assumed from the Mac.** The login name, a `~/.secrets` on the Spark, DGX OS's
   package names and what it ships, and which kernel GRUB boots were all assumptions. All but the
   last proved wrong once the box was checked, the first two silently; which kernel GRUB boots
   still isn't checked there. *Rule:* a box fact stays marked unverified until the Spark session
   checks it.
4. **Checks that couldn't fail.** The check that `agent` can't read `/home/dan/.secrets` printed
   "good" because the file didn't exist. Bootstrap's tests passed when the line they checked was
   missing, and the hold's test data never held a package in state `hi`, `iU` or `iF`. *Rule:* see
   every test and check fail once, against the bad case.
5. **Security came last.** The per-task reviews checked the spec and code quality. Security had a
   lens of its own only at the phase-end council, after bootstrap had run on the box. So its fixes
   landed in the code and the runbooks, while the box still waits for a bootstrap re-run and three
   of Dan's steps. *Rule:* review permissions, secrets and network exposure in the task that
   changes them, before it reaches the box.
6. **Fixes that made new errors.** Most reviews of a fix found something the fix itself had
   introduced. G2's new upgrade-day recovery let a moved set skip its own check, and each of G3's
   rounds left wording the next round corrected. The GRUB check changed in six commits. The first
   four reasoned from GRUB's settings, and each missed a case; the fifth read what `grub.cfg` will
   boot, tested against Ubuntu's real `grub-mkconfig`, and the sixth tightened it. *Rule:* re-read
   every sentence a fix touches against the code, check a tool's behaviour against the tool
   itself, and mark the rest unverified.
7. **Steps placed before what they need.** The plan set up the Spark session in Task 10, after Task
   9 had run in it. The first "In order" list for the runbooks missed what step 1 uses (tmux, gh,
   Claude Code, uv), and that the tailnet names don't resolve until Tailscale is joined. *Rule:* a
   step comes after everything it uses.
8. **Process slips.** One commit went in with another model's trailer and had to be amended. Shell
   expansion garbled one commit message (`cbb28b2`). The secrets hook refused shell commands that
   merely named a secret path. *Rule:* commits carry the exact trailer and are never amended without
   Dan's OK; plans, runbooks and commit messages are written with file tools (`git commit -F`).

## Carried to Phase 1

**Open decisions**

- **The unit-file model.** Dan decides before Phase 1's Task 6 writes the unit templates: keep the
  units rendered as Dan, in which case anything running as Dan can become root without a password,
  or install root-owned copies. The plan's *Open items and risks* gives both options, and
  `phase-1.md` builds the first and stops at Task 6.
- **Whether `make upgrade-gpu` runs the GRUB check itself** before it says to reboot. It is
  recommended; decide at Phase 1's pre-flight (`phase-1.md`, Task 10). Until then, `updates.md`'s
  step 5 GRUB check is run by hand, before the move and again before the reboot.

**Pending on the box.** Each gets a dated `changelog.md` entry and a line in `README.md` §Current
state when it's done.

- **Re-run `make bootstrap`**, so that `/var/lib/local-ai` becomes root's and earlyoom avoids
  `sshd.*` (`3735420`). Phase 1's Task 12, Step 1 does this, together with Phase 1's cache folders
  and needrestart override.
- **Keys-only SSH**, with the public IPv6 check ([SSH from the Mac](../how-to/ssh.md#keys-only)).
- **The Spark's GitHub token for this repository only**, with the first login revoked
  ([The Spark session](../how-to/spark-session.md#github-a-token-for-this-repository-only)).
- **The secrets guard in the Spark's Claude session**, with the Mac's global rules
  ([The Spark session](../how-to/spark-session.md#before-the-first-session), steps 2 and 3). They
  reached the runbook after the Spark's Phase 0 sessions, and nothing records them on the box.
- **Two facts to record:** Tailscale's version, and the kernel's full release string.

Phase 1's Mac → Spark switch point checks the middle three before any **[Spark]** task, and before
anything faces the network.

**Still unverified.** The plan's *To verify on the box* has the full list. Among them: upgrade day's
move of the GPU set and its recovery, untried until the first one on 2026-09-26; that GRUB boots the
newest kernel; and `agent` running a real CUDA program, which waits for Phase 1's first engine.

**Deferred minors.** Small, and none blocks anything:

- `publish-website.yml`'s checkout still keeps the job token; `persist-credentials: false` is set
  only in `ci.yml`.
- There is no pre-push hook, so the denylist scan of outgoing patches and messages, which CI can't
  run, is still done by hand before a push.
- No pattern catches a global IPv6 address (`2000::/3`, allowing the `2001:db8::/32` documentation
  prefix).
- A MAC address written without separators, or in dotted form, isn't matched.
- A tailnet IPv6 address is reported twice, as a tailnet address and as a unique-local one.
- `check-scenarios` reads only `sNN-*.md`, while the site lists every `s*.md`, so a misnamed page
  or two pages with the same ID pass.
- `versions.py` doesn't check that `advisories` is an https URL, and doesn't escape table cells.
- The Spark's arm64 gitleaks is checked against the release's own checksum file; pin its hash as CI
  pins the x64 one.
- shellcheck isn't pinned (0.11.0 on the Mac, 0.9.0 on the Spark, the runner's own in CI), and its
  file list is kept in both `ci.yml` and the `Makefile`.
- uv's `required-version` is a floor, not a pin, so a `uv self update` between upgrade days goes
  unnoticed.
- Run from a root shell, bootstrap takes root as the admin and stops partway; preflight should
  refuse it.
- Bootstrap overwrites earlyoom's dpkg conffile, which an earlyoom update can revert; a systemd
  drop-in would avoid that.
- systemd-oomd is off only because it isn't installed; mask it, and have `make doctor` check it.
- earlyoom restarts on every bootstrap re-run; a `cmp -s` guard would skip an unchanged file.
- The `adm` group gives Dan's sessions more logs than `make logs` needs (`systemd-journal` fits),
  and `spark-session.md` should say that journal output is private material.
- `secret-files.md` could refuse a key that already exists instead of appending it, and gain a
  command that removes a key, which would also give a way to rotate one.
- `tailscale.md`'s grant template keeps a home-LAN grant that does nothing without a route. Drop it
  until a route exists, and add policy tests and a note to keep Funnel off `tag:spark`.
- The hold's hint for a half-installed or reinstall-required package names only
  `dpkg --configure -a`, and apt may be needed as well.
- Dependabot's first PRs come after the merge: they should bring the two stale Actions pins up to
  date, and its first uv PR needs checking against `required-version`.

## Starting over

**On a new or rebuilt box**, follow the How-to page's [*In order*](../how-to/index.qmd#in-order)
from the top. Its *Before step 1* installs Claude Code, uv, tmux and gh, and uses the LAN aliases
until Tailscale is joined. Then come the Spark session, leak guards, bootstrap, SSH from the Mac,
Tailscale and secret files, then back to SSH's *Keys only* and the IPv6 check.
[Updates](../how-to/updates.md) is ongoing. A rebuild starts with `README.md`'s factory reset (not
yet performed on this box). On the way through the runbooks, delete the old Tailscale device before
rejoining ([Join Tailscale](../how-to/tailscale.md#rebuilding-the-box)), and move the GPU set to
current at, or right after, the first bootstrap (the plan's *Backups, recovery and upgrades*).
Record each step in `changelog.md` and `README.md` §Current state. S18 is the scenario for this.

**On the Mac:** [Leak guards](../how-to/leak-guards.md) (gitleaks and shellcheck from Homebrew, the
denylist, `make hooks` and the drill), uv, and Quarto for `make docs`. `make test lint docs` must
pass before anything else.

**Read first:** `CLAUDE.md`, with its *Lessons from Phase 0*; [the plan](plan.md); this page;
`README.md` §Current state; then the phase plan being executed, which is [Phase 1](phase-1.md)
next. The code in the repo is the as-built version. Where a listing in
[Phase 0's plan](phase-0.md) differs, it is marked *Superseded*, and the code wins.

**A fresh Claude session** reads `CLAUDE.md` by itself; point it at the plan and this page before it
proposes anything. On the Spark it also needs what [The Spark session](../how-to/spark-session.md)
sets up first: the Mac's global rules, the secrets guard and the project's private memory.
Everything private, such as full addresses, MACs, the ACL policy and each secret's record by
reference, lives in the vault's entry note, never here.
