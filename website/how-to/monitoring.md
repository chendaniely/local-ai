---
title: "Monitoring: the live view"
description: "What's happening on the Spark right now: spark watch, btop, nvtop and nvitop, dcgmi, and the engines' /metrics — and which number says why it's slow."
---

*(Added 2026-10-13, from the monitoring branch: `spark watch` and the engines' `--metrics` go out with the
next `make apply`; the TUIs are installed already — nvitop and nvtop on 2026-10-08, btop and DCGM on
2026-10-13.)*

The live view is four tools and one number per question. Everything runs on the Spark, in tmux; the Mac
reaches it through the SSH tunnel as usual.

## The tools

| Question | Tool | What it shows |
|---|---|---|
| The whole stack, at a glance | `spark watch` | memory and headroom to the brake, what's loaded, each engine's in-flight and deferred requests, the brake's hold, the last refusal |
| CPU and memory | `btop` | the usual top, with graphs |
| The GPU's processes | `nvtop` (or `nvitop`) | which process uses the GB10, and at what utilization |
| The GPU itself | `dcgmi dmon` | utilization, SM clock, power and temperature, once a second |
| One engine's counters | `curl …/metrics` | the engine's own Prometheus counters |

**On the Spark:**

```bash
spark watch                        # the live view; --once for one snapshot, --json for scripts
btop                               # CPU and memory
nvtop                              # the GPU's processes (nvitop is the same, with its own layout)
dcgmi dmon -e 203,204,155,150      # utilization %, SM clock MHz, power W, temperature °C
```

- `spark watch` refreshes every two seconds (`--interval` changes it); Ctrl-C leaves cleanly. It reports
  what it can't read and keeps showing the rest, as `spark status` does, and its problems end the same
  way: the key is in `$SPARK_API_KEY`, as for `spark status` and `make status`.
- The GPU's memory is the machine's memory: on the GB10's unified memory nvitop's NVML view reads `[N/A]`
  for it, so `free -g` (or `spark watch`'s memory line) is the memory's source, and `dcgmi dmon` the
  GPU's own.
- The engine's counters come from the engine's own port — llama-swap's `/running` gives each engine's
  port in its `proxy` field — and `spark watch` reads them for you:

  ```bash
  curl -s http://127.0.0.1:PORT/metrics | grep 'llamacpp:requests_'
  ```

  Until the next `make apply`, the engines answer `404`: render's `--metrics` is what turns the endpoint
  on. `spark watch` says so on its `problem` line until then.

## Why is it slow

| You see | It means |
|---|---|
| `deferred` above 0 on an engine | the model's slots are all busy; the request waits for one. More requests than slots is the design (the model's `parallel`), not a fault — until the wait gets long |
| `in_flight` at the slot count, GPU near 100% in `dcgmi dmon` | the model is going as fast as GB10's memory bandwidth lets it; there is nothing to fix, only to wait |
| memory near the warn line (28 GiB), or past the brake (20 GiB) | the brake will hold new loads at the brake line; `spark watch` shows the hold, and `make brake-release` clears one the brake set |
| `Swap` growing in `free -g` | the box is past its memory and generation slows a lot; unload an on-demand model, or release the hold |
| requests deferred while the GPU sits low in `dcgmi dmon` | the wait isn't in the model: a cold load (the engine still starting), a long prompt's prefill, or the disk — the engine's journal says which (`make logs`) |
| a model missing from `loaded` that should be there | its engine died or was refused; the last refusal is on `spark watch`'s `refused` line, and the journal under it |

earlyoom is the only net past the brake: if it killed an engine, its line is in the journal —
`sudo journalctl | grep -i earlyoom`.

## What isn't here yet

- **A history, and a web dashboard** (Prometheus and Grafana over the same `/metrics`): the plan's open
  items; `spark watch` is the live terminal view until then.
- **Alerts**: ntfy, Phase 2's gate events ([ntfy on the Synology](ntfy.md)) — a separate path from this
  view, by design.
