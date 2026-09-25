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
[Updates](../how-to/updates.md#upgrade-day-the-gpu-set)' steps as one command, in tmux. It stops
what uses the GPU and releases the set. It moves the set with `full-upgrade`, refusing a plan that
would remove the NVIDIA modules metapackage or install a kernel with no modules for it. It re-holds
the set, the hold and nothing else, and checks that the kernel GRUB boots has an NVIDIA module
before it asks for the reboot. After the reboot the stack comes back by itself, and `make doctor`
confirms it: the GPU on the new driver, the running kernel's modules held, a model loaded end to
end. Dependabot's PRs are merged or rebased, never squashed.

**What I see.** Each step's result as it runs, and the new kernel, driver and CUDA versions to
record in `changelog.md` and `README.md` §Current state. If it refuses, I see which package would
have gone, and nothing has moved.

**How to override.** Skip a week: the set stays held, and the next upgrade day catches up. Move the
set early only for a kernel or NVIDIA driver security fix. If a step fails or I answer no,
`make hold-gpu` holds the set as it is. A box that comes back without a GPU has its own steps in
[Updates](../how-to/updates.md#if-it-goes-wrong), including booting the previous kernel.
