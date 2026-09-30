---
title: "S23 · Upgrade day"
scenario-id: S23
phase: 1
status: built
---

**Situation.** It is Saturday, upgrade day. The held GPU set (the kernel, the NVIDIA modules built
for it, the driver and CUDA) has updates waiting, Dependabot opened its PRs on Friday, and models
may be loaded. On any other day I run `sudo apt upgrade` out of habit.

**What happens.** A routine `apt upgrade`, any day, moves nothing in the held set and restarts no
model: needrestart leaves the `local-ai-*` units alone. On upgrade day, `make upgrade-gpu` runs
[Updates](../how-to/updates.md#upgrade-day-the-gpu-set)' steps as one command, in tmux, step 5's
GRUB check included. First it checks that GRUB will boot the newest kernel, and stops there, with
the set still held and nothing moved, if not. Then it releases the set and reads apt's plan. It
refuses a plan that would remove the NVIDIA modules metapackage, install a kernel with no modules
for it, or change the driver branch. Only then does it stop llama-swap and the brake (my own GPU
jobs I stop first) and move the set with `full-upgrade`. It re-holds the set, the hold and nothing
else. Before it asks for the reboot, it checks that the newest kernel has an NVIDIA module, and that
GRUB will boot that kernel: its menu's first entry is that kernel, and nothing picks another. GRUB
does by default, but that is not yet checked on this box. *(Checked 2026-09-27, in Phase 1's Task
12: it does. Installing a kernel rebuilds the menu, so every upgrade day still checks.)* After the
reboot the stack comes back by itself, and `make doctor` confirms it: the GPU on the new driver, the
running kernel's modules held, a model loaded end to end. Doctor never loads whisper-server, which
was built on the box against the system's CUDA, so I also check that it still finds its libraries
(Updates, step 7; added 2026-09-27, from Phase 1's Task 11). Dependabot's PRs are merged or rebased,
never squashed. (Corrected 2026-09-25: this said `make upgrade-gpu` runs all but the GRUB check,
which I would run myself before it and again before the reboot. Dan decided that it runs the check
itself.)

*(Added 2026-09-28, from Phase 1's council:* a routine upgrade that moves Docker stops its
containers, and the web services may not come back by themselves, which `make doctor`'s
`stack units` line shows ([Updates](../how-to/updates.md#after-any-update-is-everything-back)).
Phase 1's routine upgrade, on 2026-09-28, moved neither Docker nor `libc6` nor `libstdc++6`, and no
library the engines or llama-swap use, so the stack serving through it shows less than it might.
Both cases wait for an upgrade that moves them.)*

**What I see.** Each step's result as it runs, and the new kernel, driver and CUDA versions to
record in `changelog.md` and `README.md` §Current state. If it refuses, I see which package would
have gone, or why GRUB wouldn't boot the newest kernel, and nothing has moved. If it stops after apt
moved part of the set, left the newest kernel without its NVIDIA module, or finds GRUB won't boot
it, it sends me to the recovery, not to a reboot. What it prints names kernels by version, never a
GRUB id or a UUID.

**How to override.** Skip a week: the set stays held, and the next upgrade day catches up. Move the
set early only for a kernel or NVIDIA driver security fix. A new driver branch is a move I plan and
make by hand. Every way out of `make upgrade-gpu` after it releases the set runs the hold, and only
the hold after apt's move is tried again. The set can still stay released: when the hold stops (a
package dpkg didn't finish, a hold that didn't take, no kernel or nothing matching), or when a
signal cuts off a hold the way out runs, the retry included. Each time, it says to run
`make hold-gpu`. After the steps by hand, `make hold-gpu` is the hold. A box that comes back without
a GPU has its own steps in [Updates](../how-to/updates.md#if-it-goes-wrong), including booting the
previous kernel.

*Status: built, 2026-09-28 (Phase 1): that day's routine `apt upgrade` and reboot passed, with the
stack serving again and no hand on it (Task 16). It becomes verified only once `make upgrade-gpu`
has moved the GPU set on a real upgrade day.*
