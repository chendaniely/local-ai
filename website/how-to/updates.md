---
title: "Updates"
description: "What you can run any time, what updates by itself, what waits for upgrade day, and how to check everything is back afterwards."
---

Everything here runs **on the Spark**, as you, unless a step says otherwise.

`sudo apt update && sudo apt upgrade` is safe to run any time, as often as habit says: it can't
move the GPU stack while the set is held. The set is held except in two cases: midway through
upgrade day, and a package of the set you have just installed, until `make hold-gpu` holds it too
([Installing something new](#any-time-apt)). Everything that needs care waits for **upgrade day, on
Saturdays**. Skipping one is fine; the next one catches up.

A version set by hand in `stack/versions.yaml`, whatever its row below, moves only to a release at
least seven days old, unless an urgent fix needs it sooner, and the commit then says so (Dan's
decision, 2026-09-28; `CLAUDE.md`, *Building it*).

| What | Comes from | When and how it updates |
|---|---|---|
| Everything else from apt | apt | Any time: `sudo apt update && sudo apt upgrade` |
| The GPU set: kernel, NVIDIA modules, driver, CUDA | apt, held | Upgrade day — [the GPU set](#upgrade-day-the-gpu-set) |
| GitHub Actions and `spark/`'s Python dependencies, in `uv.lock` and `pyproject.toml` *(this said `spark/uv.lock` alone until 2026-09-28)* | Dependabot's PRs against `main`, opened on Fridays | Upgrade day — [the automated PRs](#upgrade-day-the-automated-prs) |
| gitleaks | a direct install in `/usr/local/bin` | Upgrade day — [gitleaks](#upgrade-day-gitleaks); no package manager sees it |
| uv | the Spark: your `~/.local/bin`; the Mac: as installed there | Upgrade day, **on the Spark**: `uv self update <version>`, the version `stack/versions.yaml` pins; the Mac's uv, which the hooks also run, moves to the same version on the Mac. *(Added 2026-09-28: `agent`'s uv, in its `~/.local/bin`, came from uv's installer at its newest, 0.12.19, not the pin; `stack/versions.yaml` records it.)* |
| Python for `spark/` | `spark/.python-version` pins 3.12 | Only deliberately, on upgrade day: change the pin, and each machine rebuilds `spark/.venv` on its next `uv run` |
| llama.cpp (`llama-server`) | its release's prebuilt arm64 CUDA binaries, in `/opt/local-ai/bin/llama.cpp/<version>/`; `stack/versions.yaml` pins it | By hand, on upgrade day, one component at a time (the plan's *Weekly upgrade day*). Its runbook is written with its first bump. The code written for it moves with it: [Versions the code was written for](#versions-the-code-was-written-for) |
| llama-swap | its release binary, in `/opt/local-ai/bin/llama-swap/<version>/`; pinned | By hand, on upgrade day. Its runbook is written with its first bump. The code written for it moves with it: [Versions the code was written for](#versions-the-code-was-written-for) |
| whisper.cpp (`whisper-server`) | built on the Spark from its release, in `/opt/local-ai/bin/whisper.cpp/<version>/`; pinned by commit | By hand, on upgrade day. Its runbook is written with its first bump. The code written for it moves with it: [Versions the code was written for](#versions-the-code-was-written-for). A rebuild after the GPU set moves is [the GPU set](#upgrade-day-the-gpu-set)'s step 7 |
| Open WebUI | its container image; pinned by digest | By hand, on upgrade day. Its runbook is written with its first bump. Before any upgrade, copy its data folder, `/var/lib/local-ai/open-webui`: Open WebUI migrates its database when it starts, and a migration can't be undone |
| SearXNG | its container image; pinned by digest | By hand, on upgrade day. Its runbook is written with its first bump |
| ntfy | its container image on the Synology, pinned by digest | By hand on upgrade day, in its Compose helper, as [ntfy on the Synology](ntfy.md) §6 says |
| The desktop's snaps (browser, mail, Snap Store, firmware updater) and their runtimes | snap | By themselves, about four times a day |
| Claude Code | your `~/.local/bin` | By itself |
| Firmware | fwupd | Not automatically. Whether GIGABYTE publishes this box's firmware there is not yet checked |

## Any time: apt

**On the Spark:**

```bash
sudo apt update && sudo apt upgrade
```

`make bootstrap` holds the GPU set, so this never changes the kernel, the NVIDIA driver or CUDA;
`apt-mark showhold` lists what is held. unattended-upgrades isn't installed, so apt changes things
only when you run it. Use a terminal rather than NVIDIA's web updater: the 2026-09-23 DGX OS update
came from it, and how it treats held packages is not yet checked.

**Installing something new.** A package that belongs to the set, such as an `nvidia-*` or `cuda-*`
package or a CUDA library, arrives unheld. Hold it with the rest: `make hold-gpu`, from
`~/git/hub/local-ai` (`make hold-gpu-dry-run` previews it). If `apt install` stops with
`you have held broken packages`, the package needs a newer version of something in the held set.
The hold is doing its job: wait for upgrade day, or install the version that fits the held set.
`apt policy <package>` lists the versions, and `sudo apt install <package>=<version>` picks one.

What a routine upgrade can restart by itself:

- **Services on replaced libraries.** After every apt run, needrestart restarts the services still
  using a library the upgrade replaced. It leaves Docker, the login services and DGX OS's own
  dashboard alone. Bootstrap installs `/etc/needrestart/conf.d/local-ai.conf`, so it leaves the
  stack's `local-ai-*` units alone too, and a habitual upgrade never restarts a model mid-use.
  `sudo needrestart -r l` lists what is still waiting for a restart.
- **Containers, when Docker itself upgrades.** Docker comes from NVIDIA's repository here. An
  upgrade stops every running container, and only those with a restart policy come back by
  themselves. The stack's web services may not, whatever their policy:
  [After any update](#after-any-update-is-everything-back) says why.
- **Nothing that needs a reboot, until you reboot.** If `/var/run/reboot-required` exists afterwards,
  `cat /var/run/reboot-required.pkgs` names the package that asked. Reboot when nothing is running,
  and only once [upgrade day's step 5](#upgrade-day-the-gpu-set) checks pass: a routine upgrade can
  rebuild GRUB's menu too.

apt keeps its own record of every run, so routine updates need no notes. **On the Spark**, to see
when updates ran and what changed:

```bash
grep -A4 '^Start-Date' /var/log/apt/history.log | tail -40
```

Older runs are in the rotated `/var/log/apt/history.log.*.gz` files (read them with `zgrep`).
Upgrade day's changes also get a `changelog.md` entry.

## By themselves: snaps

The snaps here are the desktop's own; none is part of the stack. snapd refreshes them about four
times a day (`snap refresh --time` shows when), so there is nothing to run. `sudo snap refresh`
updates them now.

If something the stack depends on ever comes as a snap, **on the Spark**, hold it so it moves on
upgrade day:

```bash
sudo snap refresh --hold <name>   # stops its automatic refreshes and a plain `snap refresh`
sudo snap refresh <name>          # upgrade day: naming the snap still refreshes it
```

gitleaks has no snap (checked 2026-09-24), which is why it is a direct install.

## After any update: is everything back?

**On the Spark**, from the clone, check Phase 0's guardrails and the stack in one pass:

```bash
make doctor
```

It checks Phase 0's guardrails: the leak hooks, the GPU set held (the running kernel's modules
package included), the driver's kernel module agreeing with `nvidia-smi`, earlyoom running with the
repo's arguments, ufw on, the secrets folder closed to you, and spark's folders as bootstrap sets
them (`/var/lib/local-ai` root's, the brake's folder spark's, shared with `spark-admin`). It checks
the stack too: root's own copies of the units and the Compose project (each root's regular file or
folder, not a link, and not writable by group or others), needrestart's override installed as the
repo has it, no folder that llama-server could read a `config.ini` from (`/etc/llama.cpp` or
`/var/lib/local-ai/.config`), its three units active, llama-swap answering and refusing a call
without a key, Open WebUI and SearXNG answering, and the embeddings model answering through
llama-swap, loaded first if it wasn't. Each line is `ok` or `FAIL`, and a `FAIL` says what to do. It
changes nothing but this: it loads the embeddings model if it isn't loaded, which also clears the
last refusal record that `make status` shows. It needs no sudo, and uses your `SPARK_API_KEY`,
which [Deploy the stack](deploy.md#before-the-first-deploy) puts in the Spark's `~/.secrets`.

What brings the stack back by itself:

- the units are enabled, so a reboot starts them;
- llama-swap and the brake restart if they crash;
- needrestart leaves the units alone.

A Docker upgrade may not. The web services' unit requires Docker, so a restart of Docker restarts
it too. An upgrade that stops Docker and starts it again leaves the web services stopped, whatever
their containers' restart policy: the unit's stop removes the containers (`docker compose down`),
and nothing starts the unit again until `systemctl start local-ai-compose`. Which of the two a
Docker upgrade does is not yet tried on this box. `make doctor` shows which: its `stack units` line
names the web services' unit when it is stopped, with the command that starts it.

llama-swap preloads nothing, so after a reboot each model loads on its first request.

## Upgrade day: the GPU set

The kernel, the NVIDIA modules built for it, the driver and CUDA only work as a matched set. The
modules are compiled for one kernel and need one exact driver version. DGX OS ships them together:
the 2026-09-23 update moved the kernel from 6.11 to 7.0, the driver from 580.95 to 580.178 and CUDA
from 13.0.0 to 13.0.3 in one transaction. So they are held together and moved together. Holding
only part of the set would let a routine `apt upgrade` install a kernel with no NVIDIA module, and
the box would come back from its next reboot without a GPU.

**Moving it early.** Only a security fix in the set justifies it: a kernel fix in
[Ubuntu's security notices](https://ubuntu.com/security/notices), or a GPU display driver fix in
[NVIDIA's security bulletins](https://www.nvidia.com/en-us/security/). `agent` and `spark` both use
the GPU, so a driver privilege-escalation fix is a boundary fix too. Everything else waits for
Saturday; that wait is the cost of holding the set.

*Not yet performed on this box.* Every step below, the recovery included, is untried until the
first upgrade day.

`make upgrade-gpu` runs steps 1 to 5, llama-swap and the brake being step 1's part, as one command,
in tmux, from the clone; it refuses to start outside tmux. Step 5's GRUB check included, it runs all
of them. First the GRUB check, as step 2 says: if GRUB won't boot the newest kernel, it stops there,
with the set still held and nothing moved, and says why. Then it releases the set, reads apt's plan
and refuses it before anything moves if it breaks step 3's rule, stops llama-swap and the brake, and
moves the set (you read apt's plan and answer). Then it holds the set again with `make hold-gpu`'s
hold, runs step 5's two checks on the new newest kernel, the module and then GRUB, and says whether
to reboot: `DON'T REBOOT` names the reason, and never a GRUB id or UUID. Every way out after the
release runs the hold, and only the hold after apt's move is tried again. The set can still stay
released: when the hold stops (a package dpkg didn't finish, a hold that didn't take, no kernel or
nothing matching), or when a signal cuts off a hold the way out runs, the retry included. In each
case it says to run `make hold-gpu`. It judges whether apt moved anything by each package's state
and version, not the hold letter, and it checks that the newest kernel still has its module. If apt
moved even part of the set, left the newest kernel without a module, or GRUB failed after the move,
it sends you to [If it goes wrong](#if-it-goes-wrong) rather than to a reboot or a restart of the
stack. Only when none of these happened does it say how to start the stack again.
`make upgrade-gpu-dry-run` prints the steps without running them. The numbered steps are what it
runs, to read along with, and to do by hand if it can't; step 5's GRUB check by hand is for those
steps. (Corrected 2026-09-25: this said it would run all but the GRUB check, which you would run
before it and again before the reboot. Dan decided that it runs the check itself.)

**On the Spark**, work in tmux, from the clone. A dropped SSH session in the middle of
`full-upgrade` is the likeliest way to leave the set half-moved; in tmux the upgrade carries on,
and `tmux attach -t upgrade` brings you back:

```bash
tmux new -As upgrade
cd ~/git/hub/local-ai
```

**On the Spark**, in that session, check before `make upgrade-gpu` that the clone's `Makefile` and
`stack/host/bootstrap.sh` are as committed: its sudo runs both, as step 4's `make hold-gpu` does.
This prints nothing when they are:

```bash
git status --short -- Makefile stack/host
```

Stop your own GPU jobs first, and `agent`'s as `agent` (`sudo -iu agent`, or its own session):
`make upgrade-gpu` stops only llama-swap and the brake. Then, **on the Spark**, run it, or do the
numbered steps below by hand:

```bash
make upgrade-gpu
```

1. Stop what uses the GPU: `systemctl stop local-ai-llama-swap local-ai-brake` (the reboot starts
   them again), your own GPU jobs, and `agent`'s as `agent`.
2. First run step 5's GRUB check, so the GRUB question is settled before anything moves. If it
   doesn't pass, stop here, with the set still held and nothing moved, and bring what it printed to
   the Claude session working on the repo (the Spark's, by default) to work out why.
   `make upgrade-gpu` runs this check itself, before it releases the set. Then release the set:

   **On the Spark:**

   ```bash
   apt-mark showhold | xargs -r sudo apt-mark unhold
   ```

   Only bootstrap holds packages on this box, so this releases just the set. With nothing held it
   does nothing, so it is safe to run again. **From here until step 4, the set is free to move. If
   anything below fails, or you answer no, run `make hold-gpu` before anything else.**
3. **On the Spark**, move the set as one:

   ```bash
   sudo dpkg --configure -a && sudo apt update && sudo apt full-upgrade
   ```

   `dpkg --configure -a` finishes anything an earlier run left half-done; otherwise it does nothing.
   `full-upgrade`, not `upgrade`: moving the set can remove the modules built for the old kernel,
   and `upgrade` never removes anything. Removing the old kernel's own modules package
   (`linux-modules-nvidia-…-<old kernel>`) is expected. Read apt's plan before you answer, and
   answer **no** if any of these is true:

   - it would remove a `linux-modules-nvidia-*-nvidia-hwe-*` package with no other
     `linux-modules-nvidia-*-nvidia-hwe-*` installed in its place: that is the metapackage that
     brings in the modules for each new kernel;
   - it would install a new kernel, `linux-image-<version>`, with no `linux-modules-nvidia-*` package
     ending in that same `<version>`;
   - it would swap that metapackage for another driver branch's (`linux-modules-nvidia-580-open-…`
     removed, `linux-modules-nvidia-590-open-…` installed, say).

   In the first two cases the new kernel would boot without a GPU. Answering no installs and
   removes nothing, so re-hold with `make hold-gpu` and try again next upgrade day. The third is a
   new driver branch: answer no and re-hold. Moving to a new branch is not a routine upgrade day.
   You plan that move for a day you choose, and make it by hand. `make upgrade-gpu` refuses all
   three too.
4. Hold the new set: `make hold-gpu`. It prints `GPU set: N packages, M already held`, then
   `GPU set held: N packages`. It stops instead, naming the packages, if one isn't cleanly
   installed (it says how to finish it) or if a hold didn't take. Do what it says, then run it
   again.
5. **On the Spark**, before you reboot, check that the kernel GRUB boots has an NVIDIA module.
   "GPU set held" proves the holds took, not that the set is complete. It takes two checks. First
   the module check, on the newest kernel:

   ```bash
   k=$(linux-version list | linux-version sort --reverse | head -1)   # the newest kernel
   modinfo -k "$k" -F version nvidia                                  # the new driver's version
   ```

   If `modinfo` says `Module nvidia not found`, or any other error, **don't reboot**: go to
   [If it goes wrong](#if-it-goes-wrong).

   **On the Spark**, then the GRUB check: that GRUB will boot that same kernel. It does by default,
   as Ubuntu sets it up, but that is not yet checked on this box *(checked 2026-09-27, in Phase 1's
   Task 12: entry 0 booted the newest kernel, with the stock `default=` lines and an empty
   `grub-editenv list`)*, and more settings can change it than `/etc/default/grub` shows: some put
   another kernel first on the menu, others pick another entry. So read what GRUB will actually do,
   from the menu it boots from, which installing a kernel rebuilds, and from what it keeps between
   boots. `grub-editenv` runs without `sudo`, so nothing here can change anything:

   ```bash
   k=$(linux-version list | linux-version sort --reverse | head -1); echo "$k"   # the newest kernel
   sudo grep -m1 -E '^[[:space:]]*linux[[:space:]]' /boot/grub/grub.cfg          # entry 0's kernel
   sudo grep 'default=' /boot/grub/grub.cfg                                       # the entry GRUB starts
   grub-editenv list                                                              # what GRUB kept
   ```

   The check passes when all three of these are true:

   - entry 0's `linux` line names `vmlinuz-` followed by the version `echo` printed;
   - the `default=` lines are exactly the stock two, both `set default=`: one is
     `"${next_entry}"`, and the other is `"0"`, or `"${saved_entry}"` with `grub-editenv`
     printing no `saved_entry=` with a value, or `saved_entry=0`. A third line, or a bare
     `default=` without `set`, fails;
   - `grub-editenv` prints no `next_entry=` and no `prev_entry=` with a value. An empty
     `next_entry=` is what a used `grub-reboot` leaves behind, and GRUB ignores it. A
     `prev_entry` becomes the next entry when `initrdfail=1`: unlikely on a box that booted, but
     cheap to rule out.

   `make upgrade-gpu` runs this same check itself after the move, before it says to reboot.

   Anything else, or any of these commands failing: **don't reboot yet**, and bring what they
   printed to the Claude session working on the repo (the Spark's, by default). Much of it carries
   the root filesystem's UUID: `root=UUID=…`, or
   `root=PARTUUID=…`, in the `linux` lines, and every entry id (`gnulinux-simple-…`,
   `gnulinux-advanced-…`, `gnulinux-<version>-advanced-…`), a default, `saved_entry` or
   `next_entry` that is an id included. Write "an id" in place of each UUID and PARTUUID when you
   bring it over, and never paste one into the repo. **On the Spark**, to see which kernel GRUB
   would start instead, list the menu, each entry followed by its `linux` line:

   ```bash
   sudo grep -E '^[[:space:]]*(menuentry|submenu|linux)[[:space:]]' /boot/grub/grub.cfg
   ```

   GRUB starts `next_entry` if it has a value (or `prev_entry`, when `initrdfail=1`), otherwise the
   default, where `"${saved_entry}"` means `saved_entry`'s value. A number counts the menu's
   top-level entries from 0, the submenu being one of them, and `1>2` is the third entry inside the
   second. A title or an id is looked up at the top level only, so an entry inside the submenu
   needs the submenu's in front, joined by `>`
   (`gnulinux-advanced-…>gnulinux-<version>-advanced-…`). `10_linux` adds the submenu to a bare
   title when it builds the menu, but not to a bare id. When nothing matches, GRUB starts entry 0.
   Reboot only when both checks pass.
6. Reboot: `sudo reboot`. It asks for your password again: `make upgrade-gpu` and `make hold-gpu`
   end with `sudo -k`, which forgets sudo's cached credential in that terminal.
7. **On the Spark**, check, once you are back in:

   ```bash
   uname -r                                                            # the new kernel
   nvidia-smi --query-gpu=name,driver_version --format=csv,noheader   # the GPU, on the new driver
   modinfo -F version nvidia                                           # the same version as nvidia-smi's driver
   apt-mark showhold | grep -- "-$(uname -r)$"                         # the running kernel's NVIDIA modules package: held
   ```

   If the last line prints nothing, the running kernel's modules aren't held: run `make hold-gpu`
   and check again. Then check whisper-server, which `make doctor` never loads. It was built on
   this box against the system's CUDA (Phase 1, Task 11), while llama.cpp carries its own CUDA
   runtime, so a CUDA move can leave whisper-server without a library:

   ```bash
   ldd /opt/local-ai/bin/whisper.cpp/*/whisper-server | grep 'not found' || echo "all libraries found"
   ```

   Expected: `all libraries found`. A `not found` line means rebuild it the way
   [Phase 1's plan](../design/phase-1.md), Task 11 Step 4, built it, into the same folder. Then
   `make doctor`: every line `ok`.
8. Record the new kernel, driver and CUDA versions in `changelog.md`, and bring the GPU set's line
   in `README.md` §Current state up to date.

### If it goes wrong

**You answered no, or a step failed before the reboot.** Re-hold first: `make hold-gpu`. If it
stops on a package that isn't cleanly installed, do what it says, then run it again.
`make upgrade-gpu` runs the hold again by itself on its way out, so `make hold-gpu` by hand is for
the manual steps, or for when its own hold stopped or was cut off. If step 3 changed anything
before it stopped, run step 5 before any reboot, both its checks. Once both pass, carry on at
step 6. If the module is missing, go to the next paragraph; if only the GRUB check fails, don't
reboot yet, as step 5 says: a `DON'T REBOOT` from `make upgrade-gpu` that names GRUB, `grub.cfg`
or `grubenv`, or says it can't read one of them, is that case.

**Step 5's module check failed, `nvidia-smi` fails after the reboot, or `make upgrade-gpu` said
`DON'T REBOOT` because the newest kernel has no NVIDIA module, or that apt may have moved the
set.** The likeliest cause is a kernel with no NVIDIA module, from a set that didn't finish moving.
The box still boots and SSH still works: nothing on the network path needs the GPU. Every command
here is safe to run again:

1. **On the Spark**, release the set and finish the move, answering as step 3 says:

   ```bash
   cd ~/git/hub/local-ai
   apt-mark showhold | xargs -r sudo apt-mark unhold
   sudo dpkg --configure -a && sudo apt update && sudo apt full-upgrade
   ```

2. **On the Spark**, if the modules metapackage was removed, put it back. apt's log names it;
   take the name from there, never from memory. If it prints more than one, take the one that
   matches your driver (`dpkg -l 'nvidia-driver-*'`):

   ```bash
   grep -ho 'linux-modules-nvidia-[^ :,]*-nvidia-hwe-[^ :,]*' /var/log/apt/history.log | sort -u
   sudo apt install <the name it printed>
   ```

3. Run step 5 again, both its checks. Once both pass: `make hold-gpu`, `sudo reboot`, then step 7's
   checks.

**The quick way back, when the driver didn't move, on the Spark.** If only the kernel moved, the
previous kernel still has its module:

```bash
p=$(linux-version list | linux-version sort --reverse | sed -n 2p)   # the previous kernel
modinfo -k "$p" -F version nvidia                                    # prints the installed driver's version?
```

If it prints the version of the driver installed now (`dpkg -l 'nvidia-driver-*'` shows it), boot
that kernel. It needs a keyboard and display at the box: as it starts, press Esc to show GRUB's
hidden menu, then choose *Advanced options* and the previous kernel. If the firmware's setup opens
instead, leave it without saving and press Esc a moment later. Neither keypress is tried on this
box yet. When the driver moved too, as it did on 2026-09-23 (which also removed the old kernel's
modules), the previous kernel won't help.

*Not yet performed on this box*, like every step above.

## Upgrade day: the automated PRs

Dependabot opens its PRs on Fridays, against `main`: up to three each for the GitHub Actions pins
and for `spark/`'s Python dependencies. On upgrade day, for each one:

1. **On github.com**, read what it bumps and its release notes, and let CI finish green. A PR that
   moves uvicorn's pin, or adds a package to the lock or drops one, fails
   `spark/tests/test_tested_against.py` on purpose until its companion change joins it
   ([Versions the code was written for](#versions-the-code-was-written-for)). That one takes step 2
   first; any other PR merges only green.
2. *Only for a PR that fails on purpose* (added 2026-10-07; *not yet performed*: no such PR has
   come yet): the companion change goes on Dependabot's own branch, in the same PR. **On the
   Spark**, in the clone, with nothing uncommitted, check out the branch the PR names, such as
   `dependabot/uv/spark/uvicorn-0.55.0`, in place of `<branch>` (the same name the scan below calls
   `<branch>`):

   ```bash
   git fetch origin
   git switch <branch>
   ```

   There, make the change the failing test names: for uvicorn, everything
   [Versions the code was written for](#versions-the-code-was-written-for) lists, with its tests in
   the order it gives; for a package added or dropped, the list in
   `test_the_lock_adds_only_uvicorn_and_starlette`, once you have read why it came or went. Run
   `make test`, and commit the files by path. Then, still **on the Spark**, after
   [the scan before every push](leak-guards.md#before-every-push), push it to the PR's branch and
   come back to your own (a Claude session pushes only with Dan's OK):

   ```bash
   git push origin HEAD
   git switch -
   ```

   CI then runs on the whole change. Dependabot stops rebasing a PR that carries someone else's
   commit, so merge it after the day's other PRs. If it conflicts with one merged before it,
   comment `@dependabot recreate` on it, which drops the companion commit, and do this step again.
3. **On github.com**, read the PR's commit messages, then merge it with a merge commit or a rebase.
   Never squash it: a squash can paste the release notes into the message, CI scans every commit
   message with the repo's patterns, and once merged, a finding in history can't be taken back or
   marked allowed.

They don't touch `stack/versions.yaml`, the workflows' `version:` inputs, uv's `required-version`
or the gitleaks pin; those still move by hand.

*(Corrected 2026-09-28, from Phase 1's council: this said the uv PRs update `spark/uv.lock`.)* A
uv PR can change `spark/pyproject.toml` as well as, or instead of, the lock: PR #3, on 2026-09-25,
raised the `[build-system]` floor on hatchling there and left `uv.lock` alone. The same updater
could widen `huggingface_hub>=0.34,<2`, the cap Task 8's review set when the lock had taken 2.0.0,
a new major on a new HTTP stack, once a 2.x release is a week old. So read a uv PR's
`pyproject.toml` diff too, and treat one that moves a cap as a decision of its own, not a routine
merge.

The uv PRs propose only releases at least seven days old. `spark/pyproject.toml`
locks nothing newer (`exclude-newer = "7 days"`, a rolling window), and `.github/dependabot.yml`
waits as long (Dan's decision, 2026-09-27): a bad or compromised upload is most often caught in its
first days. Dependabot reads both from `main`, which gets them with Phase 1's merge, so no uv PR had
run under the window by 2026-09-28. An update by hand follows the same window. **On the Spark**,
in the clone (or on the Mac, when the Mac's session holds the branch), this takes the newest
release of `<name>` that is a week old:

```bash
uv lock --project spark --upgrade-package <name>
```

A fix you need sooner, such as a security release, gets an exception for that one package: add
`exclude-newer-package = { <name> = "<now, e.g. 2026-09-27T12:00:00Z>" }` under `[tool.uv]` in
`spark/pyproject.toml`, run the same `uv lock`, and take the line out once that release is a week
old; before then, a plain `uv lock` would move the package back. Dependabot's security PRs don't
wait, so one for a release younger than a week fails to lock until then.

### Versions the code was written for

*(Added 2026-10-07, Phase 2a's Task 3.)* Some modules rely on one release of a component: its API,
its config, its options or its internals. Each names it in a `TESTED_AGAINST` constant, a
component of `stack/versions.yaml` by its name and a Python package as `pypi:<name>`.
`spark/tests/test_tested_against.py` checks every one against `stack/versions.yaml` and against
the installed packages, which `uv run --frozen` installs at the lock's versions, so a bump that
leaves one behind fails `make test`, and CI. Moving a component in `stack/versions.yaml` means
checking each module that names it against the new release, then moving its constant with it.
**On the Spark**, in the clone, this lists every constant and the module it is in:

```bash
grep -rn --include='*.py' '^TESTED_AGAINST' spark/src spark/tests
```

`test_tested_against.py` also holds the lock's list of packages, so a PR that adds a package to the
lock or drops one fails CI until that list changes too. Changing it is a decision of its own: by
hand, in one commit with the lock; for Dependabot's PR, on its branch, in the same PR (step 2
above).

**uvicorn is pinned exactly**, `uvicorn==0.54.0` without extras, where every other dependency
takes a range (Starlette's, `>=1.3.1,<2`, starts at 1.3.1, which fixed the last advisory against
1.x as of 2026-10-07). The front and the gate are to serve through two modules that rely on
uvicorn's internals, which no release promises to keep (since Phase 2a's Task 9):
`spark/src/spark/protocols.py` subclasses its h11 protocol, and `spark/src/spark/serve.py` replaces
its server's signal handling and uses the server's `started` and `should_exit` flags. Each module's
docstring names what it relies on. So only the uvicorn they were tested with may run, and the pin
is that release. A uvicorn bump changes four things together: the pin in `spark/pyproject.toml`,
`pypi:uvicorn` in `protocols.TESTED_AGAINST` and in `serve.TESTED_AGAINST`, and the version
`test_uvicorn_is_pinned_exactly` expects. By hand, all four go in one commit with the lock, made
with the `uv lock` above. Dependabot's PR brings the pin and the lock, so the other three go on its
branch, in the same PR (step 2 above); a PR that moves the pin alone fails CI, on purpose. Either
way, **on the Spark**, in the clone, with the bump checked out, run `test_protocols.py` and
`test_sockets.py`, which run a real uvicorn through both modules, before anything else, and the
rest after them:

```bash
uv run --frozen --project spark pytest spark/tests/test_protocols.py spark/tests/test_sockets.py
make test
```

## Upgrade day: gitleaks

gitleaks is a direct install, so apt and snap never update it. On upgrade day, look at its
[releases](https://github.com/gitleaks/gitleaks/releases). If there is a newer version at least
seven days old (the rule above the table):

1. **On the Mac**, bump it everywhere it is pinned, in one commit: `stack/versions.yaml`; in
   `.github/workflows/ci.yml`, the URL in the "download gitleaks" step and the `linux_x64` checksum
   in the "check gitleaks against its pinned checksum" step (the checksum is in the release's
   `checksums.txt`); and the version in [Leak guards](leak-guards.md)' install block. Then
   `brew upgrade gitleaks` for the Mac's own copy. Push it, after
   [the scan before every push](leak-guards.md#before-every-push), and pull it on the Spark.
2. **On the Spark**, install it over the old one. Set `v` to the new version; the checksum is
   checked before anything is installed:

   ```bash
   v=8.30.1   # the new version, as stack/versions.yaml pins it
   cd "$(mktemp -d)"
   curl -fsSLO "https://github.com/gitleaks/gitleaks/releases/download/v$v/gitleaks_${v}_linux_arm64.tar.gz" &&
     curl -fsSLO "https://github.com/gitleaks/gitleaks/releases/download/v$v/gitleaks_${v}_checksums.txt" &&
     sha256sum --ignore-missing -c "gitleaks_${v}_checksums.txt" &&
     tar xzf "gitleaks_${v}_linux_arm64.tar.gz" gitleaks &&
     sudo install -m 0755 gitleaks /usr/local/bin/
   cd -
   gitleaks version   # the new version
   ```

3. **On the Spark**, `make test` — the hook tests run the real gitleaks — and a `changelog.md`
   entry. Then **on the Mac**, in the clone, `make test` too: step 1 moved the Mac's own copy, and
   its hooks use it.
