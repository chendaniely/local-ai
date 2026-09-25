---
title: "Updates"
description: "What you can run any time, what updates by itself, what waits for upgrade day, and how to check everything is back afterwards."
---

`sudo apt update && sudo apt upgrade` is safe to run any time, as often as habit says: it can't
move the GPU stack. Everything that needs care waits for **upgrade day, on Saturdays**. Skipping one is fine; the next
one catches up.

| What | Comes from | When and how it updates |
|---|---|---|
| Everything else from apt | apt | Any time: `sudo apt update && sudo apt upgrade` |
| The GPU set: kernel, NVIDIA modules, driver, CUDA | apt, held | Upgrade day — [the GPU set](#upgrade-day-the-gpu-set) |
| gitleaks | a direct install in `/usr/local/bin` | Upgrade day — [gitleaks](#upgrade-day-gitleaks); no package manager sees it |
| uv | your `~/.local/bin` | Upgrade day: `uv self update <version>`, the version `stack/versions.yaml` pins |
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

What a routine upgrade can restart by itself:

- **Services on replaced libraries.** After every apt run, needrestart restarts the services still
  using a library the upgrade replaced. It leaves Docker, the login services and DGX OS's own
  dashboard alone. From Phase 1 it leaves the stack alone too, so a habitual upgrade never restarts
  a model mid-use. `sudo needrestart -r l` lists what is still waiting for a restart.
- **Containers, when Docker itself upgrades.** Docker comes from NVIDIA's repository here. An
  upgrade stops every running container, and only those with a restart policy come back by
  themselves.
- **Nothing that needs a reboot, until you reboot.** If `/var/run/reboot-required` exists afterwards,
  `cat /var/run/reboot-required.pkgs` names the package that asked; reboot when nothing is running.

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

Move the set sooner than upgrade day when a serious kernel security fix lands. Those fixes waiting
for you is the cost of holding it.

1. Stop anything using the GPU. Nothing does yet; from Phase 1, `make upgrade-gpu` will do steps 1–4.
2. Release the set and move it as one:

   ```bash
   sudo apt-mark unhold $(apt-mark showhold) && sudo apt update && sudo apt full-upgrade
   ```

   `full-upgrade`, not `upgrade`: moving the set can mean removing the modules built for the old
   kernel, and `upgrade` never removes anything. Only bootstrap holds packages on this box, so
   releasing everything held releases just the set.
3. Hold the new set, from `~/git/hub/local-ai`: `make bootstrap`. It is safe to re-run, and it holds
   the set as it is installed now.
4. Reboot: `sudo reboot`.
5. Check, once you are back in:

   ```bash
   uname -r                                                            # the new kernel
   nvidia-smi --query-gpu=name,driver_version --format=csv,noheader   # the GPU, on the new driver
   apt-mark showhold | grep -c .                                       # held again: not zero
   ```

   From Phase 1, run `make doctor` too; it loads a model end to end.
6. Record the new kernel, driver and CUDA versions in `changelog.md`.

**If `nvidia-smi` fails after the reboot**, the driver didn't load. The box still boots and SSH still
works — nothing on the network path needs the GPU. The likeliest cause is a set that didn't finish
moving. Release it, finish it, hold it and reboot:

```bash
sudo apt-mark unhold $(apt-mark showhold) && sudo apt update && sudo apt full-upgrade && make bootstrap && sudo reboot
```

Booting the previous kernel may not help: the 2026-09-23 update removed the old kernel's NVIDIA
modules. *Not yet performed on this box* — the first upgrade day is the test of this section.

## Upgrade day: gitleaks

gitleaks is a direct install, so apt and snap never update it. On upgrade day, look at its
[releases](https://github.com/gitleaks/gitleaks/releases). If there is a newer version:

1. On the Mac, bump it everywhere it is pinned, in one commit: `stack/versions.yaml`; the download
   URL and the `linux_x64` checksum in `.github/workflows/ci.yml` (the checksum is in the release's
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
