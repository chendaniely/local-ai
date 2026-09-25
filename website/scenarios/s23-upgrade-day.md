---
title: "S23 · Upgrade day"
scenario-id: S23
phase: 1
status: planned
---

**Situation.** It is Saturday, upgrade day. The held GPU set (the kernel, the NVIDIA modules built
for it, the driver and CUDA) has updates waiting, Dependabot opened its PRs on Friday, and models
may be loaded. On any other day I run `sudo apt upgrade` out of habit.

**What happens.** A routine `apt upgrade`, any day, moves nothing in the held set and restarts no
model: needrestart leaves the `local-ai-*` units alone. On upgrade day, `make upgrade-gpu` runs
[Updates](../how-to/updates.md#upgrade-day-the-gpu-set)' steps as one command, in tmux. It releases
the set and reads apt's plan first. It refuses a plan that would remove the NVIDIA modules
metapackage, install a kernel with no modules for it, or change the driver branch. Only then does it
stop what uses the GPU and move the set with `full-upgrade`. It re-holds the set, the hold and
nothing else. Before it asks for the reboot, it checks that the newest kernel has an NVIDIA module.
GRUB boots that kernel by default, but that is not yet checked on this box. After the reboot the
stack comes back by itself, and `make doctor` confirms it: the GPU on the new driver, the running
kernel's modules held, a model loaded end to end. Dependabot's PRs are merged or rebased, never
squashed.

**What I see.** Each step's result as it runs, and the new kernel, driver and CUDA versions to
record in `changelog.md` and `README.md` §Current state. If it refuses, I see which package would
have gone, and nothing has moved. If it stops after apt moved part of the set, or left the newest
kernel without its NVIDIA module, it sends me to the recovery, not to a reboot.

**How to override.** Skip a week: the set stays held, and the next upgrade day catches up. Move the
set early only for a kernel or NVIDIA driver security fix. A new driver branch is a move I plan and
make by hand. Every way out of `make upgrade-gpu` runs the hold, and only the hold after apt's move
is tried again. The set can still stay released: when the hold stops (a package dpkg didn't finish,
a hold that didn't take, no kernel or nothing matching), or when a signal cuts off a hold the way
out runs, the retry included. Each time, it says to run `make hold-gpu`. After the steps by hand,
`make hold-gpu` is the hold. A box that comes back without a GPU has its own steps in
[Updates](../how-to/updates.md#if-it-goes-wrong), including booting the previous kernel.
