---
title: "Updates"
description: "What you can run any time, what updates by itself, what waits for upgrade day, and how to check everything is back afterwards."
---

`sudo apt update && sudo apt upgrade` is safe to run any time, as often as habit says: it can't
move the GPU stack while the set is held. The set is held except in two cases: midway through
upgrade day, and a package of the set you have just installed, until `make hold-gpu` holds it too
([Installing something new](#any-time-apt)). Everything that needs care waits for **upgrade day, on
Saturdays**. Skipping one is fine; the next one catches up.

| What | Comes from | When and how it updates |
|---|---|---|
| Everything else from apt | apt | Any time: `sudo apt update && sudo apt upgrade` |
| The GPU set: kernel, NVIDIA modules, driver, CUDA | apt, held | Upgrade day — [the GPU set](#upgrade-day-the-gpu-set) |
| GitHub Actions and `spark/uv.lock` | Dependabot's PRs against `main`, opened on Fridays | Upgrade day — [the automated PRs](#upgrade-day-the-automated-prs) |
| gitleaks | a direct install in `/usr/local/bin` | Upgrade day — [gitleaks](#upgrade-day-gitleaks); no package manager sees it |
| uv | your `~/.local/bin` | Upgrade day: `uv self update <version>`, the version `stack/versions.yaml` pins |
| Python for `spark/` | `spark/.python-version` pins 3.12 | Only deliberately, on upgrade day: change the pin, and each machine rebuilds `spark/.venv` on its next `uv run` |
| The desktop's snaps (browser, mail, Snap Store, firmware updater) and their runtimes | snap | By themselves, about four times a day |
| Claude Code | your `~/.local/bin` | By itself |
| Firmware | fwupd | Not automatically. Whether GIGABYTE publishes this box's firmware there is not yet checked |

## Any time: apt

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
  dashboard alone. From Phase 1 it leaves the stack alone too, so a habitual upgrade never restarts
  a model mid-use. `sudo needrestart -r l` lists what is still waiting for a restart.
- **Containers, when Docker itself upgrades.** Docker comes from NVIDIA's repository here. An
  upgrade stops every running container, and only those with a restart policy come back by
  themselves.
- **Nothing that needs a reboot, until you reboot.** If `/var/run/reboot-required` exists afterwards,
  `cat /var/run/reboot-required.pkgs` names the package that asked. Reboot when nothing is running,
  and only once [upgrade day's step 5](#upgrade-day-the-gpu-set) checks pass: a routine upgrade can
  rebuild GRUB's menu too.

apt keeps its own record of every run, so routine updates need no notes. To see when updates ran
and what changed:

```bash
grep -A4 '^Start-Date' /var/log/apt/history.log | tail -40
```

Older runs are in the rotated `/var/log/apt/history.log.*.gz` files (read them with `zgrep`).
Upgrade day's changes also get a `changelog.md` entry.

## By themselves: snaps

The snaps here are the desktop's own; none is part of the stack. snapd refreshes them about four
times a day (`snap refresh --time` shows when), so there is nothing to run. `sudo snap refresh`
updates them now.

If something the stack depends on ever comes as a snap, hold it so it moves on upgrade day:

```bash
sudo snap refresh --hold <name>   # stops its automatic refreshes and a plain `snap refresh`
sudo snap refresh <name>          # upgrade day: naming the snap still refreshes it
```

gitleaks has no snap (checked 2026-09-24), which is why it is a direct install.

## After any update: is everything back?

Nothing on the box serves anything yet, so today there is nothing to bring back. Phase 1 adds the
stack, and with it the steps for this section. The plan requires that the stack comes back by
itself after a routine upgrade, a Docker upgrade, a reboot or an upgrade day, and that `make doctor`
confirms it.

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

Work in tmux, from the clone. A dropped SSH session in the middle of `full-upgrade` is the likeliest
way to leave the set half-moved; in tmux the upgrade carries on, and `tmux attach -t upgrade` brings
you back:

```bash
tmux new -As upgrade
cd ~/git/hub/local-ai
```

1. Stop anything using the GPU. Nothing does yet; from Phase 1, `make upgrade-gpu` will run these
   steps as one command, all but step 5's GRUB check, which you run before it and again before the
   reboot.
2. First run step 5's GRUB check, so the GRUB question is settled before anything moves. If it
   doesn't pass, stop here, with the set still held and nothing moved, and bring what it printed to
   the Mac session to work out why. Then release the set:

   ```bash
   apt-mark showhold | xargs -r sudo apt-mark unhold
   ```

   Only bootstrap holds packages on this box, so this releases just the set. With nothing held it
   does nothing, so it is safe to run again. **From here until step 4, the set is free to move. If
   anything below fails, or you answer no, run `make hold-gpu` before anything else.**
3. Move the set as one:

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
   You plan that move for a day you choose, and make it by hand.
4. Hold the new set: `make hold-gpu`. It prints `GPU set: N packages, M already held`, then
   `GPU set held: N packages`. It stops instead, naming the packages, if one isn't cleanly
   installed (it says how to finish it) or if a hold didn't take. Do what it says, then run it
   again.
5. Before you reboot, check that the kernel GRUB boots has an NVIDIA module. "GPU set held" proves
   the holds took, not that the set is complete. It takes two checks. First the module check, on
   the newest kernel:

   ```bash
   k=$(linux-version list | linux-version sort --reverse | head -1)   # the newest kernel
   modinfo -k "$k" -F version nvidia                                  # the new driver's version
   ```

   If `modinfo` says `Module nvidia not found`, or any other error, **don't reboot**: go to
   [If it goes wrong](#if-it-goes-wrong).

   Then the GRUB check: that GRUB will boot that same kernel. It does by default, as Ubuntu sets it
   up, but that is not yet checked on this box, and more settings can change it than
   `/etc/default/grub` shows: some put another kernel first on the menu, others pick another entry.
   So read what GRUB will actually do, from the menu it boots from, which installing a kernel
   rebuilds, and from what it keeps between boots. `grub-editenv` runs without `sudo`, so nothing
   here can change anything:

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

   Anything else, or any of these commands failing: **don't reboot yet**, and bring what they
   printed to the Mac session. Much of it carries the root filesystem's UUID: `root=UUID=…`, or
   `root=PARTUUID=…`, in the `linux` lines, and every entry id (`gnulinux-simple-…`,
   `gnulinux-advanced-…`, `gnulinux-<version>-advanced-…`), a default, `saved_entry` or
   `next_entry` that is an id included. Write "an id" in place of each UUID and PARTUUID when you
   bring it over, and never paste one into the repo. To see which kernel GRUB would start instead,
   list the menu, each entry followed by its `linux` line:

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
6. Reboot: `sudo reboot`.
7. Check, once you are back in:

   ```bash
   uname -r                                                            # the new kernel
   nvidia-smi --query-gpu=name,driver_version --format=csv,noheader   # the GPU, on the new driver
   modinfo -F version nvidia                                           # the same version as nvidia-smi's driver
   apt-mark showhold | grep -- "-$(uname -r)$"                         # the running kernel's NVIDIA modules package: held
   ```

   If the last line prints nothing, the running kernel's modules aren't held: run `make hold-gpu`
   and check again. From Phase 1, run `make doctor` too; it loads a model end to end.
8. Record the new kernel, driver and CUDA versions in `changelog.md`, and bring the GPU set's line
   in `README.md` §Current state up to date.

### If it goes wrong

**You answered no, or a step failed before the reboot.** Re-hold first: `make hold-gpu`. If it
stops on a package that isn't cleanly installed, do what it says, then run it again. If step 3
changed anything before it stopped, run step 5 before any reboot, both its checks. Once both pass,
carry on at step 6. If the module is missing, go to the next paragraph; if only the GRUB check
fails, don't reboot yet, as step 5 says.

**Step 5's module check failed, or `nvidia-smi` fails after the reboot.** The likeliest cause is
a kernel with no NVIDIA module, from a set that didn't finish moving. The box still boots and SSH
still works: nothing on the network path needs the GPU. Every command here is safe to run again:

1. Release the set and finish the move, answering as step 3 says:

   ```bash
   cd ~/git/hub/local-ai
   apt-mark showhold | xargs -r sudo apt-mark unhold
   sudo dpkg --configure -a && sudo apt update && sudo apt full-upgrade
   ```

2. If the modules metapackage was removed, put it back. apt's log names it; take the name from
   there, never from memory. If it prints more than one, take the one that matches your driver
   (`dpkg -l 'nvidia-driver-*'`):

   ```bash
   grep -ho 'linux-modules-nvidia-[^ :,]*-nvidia-hwe-[^ :,]*' /var/log/apt/history.log | sort -u
   sudo apt install <the name it printed>
   ```

3. Run step 5 again, both its checks. Once both pass: `make hold-gpu`, `sudo reboot`, then step 7's
   checks.

**The quick way back, when the driver didn't move.** If only the kernel moved, the previous kernel
still has its module:

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
and for `spark/uv.lock`. On upgrade day, for each one:

1. Read what it bumps and its release notes, and let CI finish green.
2. Read the PR's commit message, then merge it with a merge commit or a rebase. Never squash it: a
   squash can paste the release notes into the message, CI scans every commit message with the
   repo's patterns, and once merged, a finding in history can't be taken back or marked allowed.

They don't touch `stack/versions.yaml`, the workflows' `version:` inputs, uv's `required-version`
or the gitleaks pin; those still move by hand.

## Upgrade day: gitleaks

gitleaks is a direct install, so apt and snap never update it. On upgrade day, look at its
[releases](https://github.com/gitleaks/gitleaks/releases). If there is a newer version:

1. On the Mac, bump it everywhere it is pinned, in one commit: `stack/versions.yaml`; in
   `.github/workflows/ci.yml`, the URL in the "download gitleaks" step and the `linux_x64` checksum
   in the "check gitleaks against its pinned checksum" step (the checksum is in the release's
   `checksums.txt`); and the version in [Leak guards](leak-guards.md)' install block. Then
   `brew upgrade gitleaks` for the Mac's own copy.
2. On the Spark, install it over the old one. Set `v` to the new version; the checksum is checked
   before anything is installed:

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

3. `make test` — the hook tests run the real gitleaks — and a `changelog.md` entry.
