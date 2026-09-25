---
title: "Phase 1 — implementation plan"
description: "First milestone: models served on brightroar, reachable from Open WebUI on the phone and from pi on the Mac, with a minimal brake and launch check."
date: 2026-09-23
---

# Phase 1 — First milestone: web UI + pi — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax. Every task is labelled **[Mac]**, **[Spark]** or **[Dan]**; ⇄ marks a
> machine switch (one session at a time). Phase 0 must be done first. This plan is revised after
> Phase 0's review if anything learned there changes it (see the plan's Revisions). Revised
> 2026-09-25 with Phase 0's lessons: Task 10 is new, and each task after it is numbered one higher.

**Goal:** Four models served on `brightroar` through llama-swap — a resident vision chat model,
embeddings, speech-to-text and a starter coder — reachable from Open WebUI on Dan's phone (HTTPS via
`tailscale serve`) and from pi on the Mac and in tmux as `agent`, protected by a minimal memory brake
and a launch check that refuses loads that don't fit. A routine `apt upgrade` or a reboot leaves it
serving, upgrade day moves the held GPU set in one command, and `make doctor` checks it all.

**Architecture:** `stack/models.yaml` is the single source of truth. The `spark` CLI renders it into
a llama-swap config, systemd units and a Compose file, validates them, and deploys them under
`/opt/local-ai/`. llama-swap (as the `spark` user, 127.0.0.1:9100, API keys required) starts every
engine through `spark launch`, which refuses a load when the brake holds or the model doesn't fit,
and marks the engine as the first thing the kernel or earlyoom should kill. `spark brake` watches
`MemAvailable` and unloads models before the box reaches the freeze band. Open WebUI and SearXNG run
under Compose (host networking, bound to 127.0.0.1) from a root-owned unit. Updates don't take it
down: bootstrap tells needrestart to leave the `local-ai-*` units alone, `make upgrade-gpu` moves the
GPU set on upgrade day through bootstrap's hold, and `make doctor` checks Phase 0's guardrails and
the stack in one pass.

**Tech Stack:** Python ≥3.12 via uv · PyYAML · huggingface_hub (downloads only) · stdlib `urllib` ·
pytest · llama-swap v257 · llama.cpp b11146 (v0.5.0, prebuilt arm64 + CUDA 13.4) · whisper.cpp
v1.9.4 (built for `121a-real`) · Open WebUI v0.11.4 (standard image) · SearXNG · systemd · polkit ·
Tailscale serve · pi 0.85.1.

**Spec:** [`website/design/plan.md`](plan.md) — Phase 1, *Admission and memory rules*, *Components*,
*Users, access and security*, *Deploy workflow*, *Where work runs*.

## Global Constraints

- Everything in Phase 0's Global Constraints still applies (public repo, private context, secrets by
  reference, uv only, make 3.81, commits with 🤖 + trailer, checkpoint commit per task on branch
  `phase-1`, push only with Dan's explicit OK, docs-must-be-true sync rule).
- **Ports, all bound to 127.0.0.1:** llama-swap `9100` · Open WebUI `3000` · SearXNG `8888` · engines
  from `5800` (llama-swap's `${PORT}`). Every one is set explicitly — Open WebUI, SearXNG and
  llama-server all default to 8080. `tailscale serve` maps HTTPS 443 → 127.0.0.1:3000.
- **Paths:** `/opt/local-ai/{app,bin,etc,python}` · `/etc/local-ai/secrets/*.env` ·
  `/var/lib/local-ai/{hf,open-webui,searxng,brake,cache,cuda-cache}` · `HF_HOME=/var/lib/local-ai/hf`.
  `/var/lib/local-ai` is `spark`'s home but root's (since Phase 0's review), and `spark` writes only
  inside its children. So nothing may cache under `$HOME`: a unit that runs engines or pulls models
  sets `XDG_CACHE_HOME=/var/lib/local-ai/cache` and `CUDA_CACHE_PATH=/var/lib/local-ai/cuda-cache`,
  two folders bootstrap gives `spark` (Task 6).
- **Budget (from `stack/models.yaml`):** allocatable 102 GiB (reported; measured later) · reserve
  24 GiB · warn 28 GiB · brake 20 GiB · poll 250 ms. The static model set must fit
  `allocatable − reserve`.
- **Never put a secret in a llama-swap `cmd`** — `GET /running` shows commands unredacted. Keys reach
  llama-swap only as `${env.LLAMASWAP_KEY_*}` from `/etc/local-ai/secrets/llama-swap.env`. Never name a
  key variable `LLAMA_API_KEY` (llama-server reads it). No `--api-key` on engines (llama-swap forwards
  the client's header), and no engine holds a key at all: every engine would inherit llama-swap's
  environment, so `spark launch` drops each `LLAMASWAP_KEY_*` variable before it starts one (Task 2).
- llama-swap silently ignores unknown config keys — every config change is followed by a start and
  `GET /running`, not just `-validate`.
- **On the Spark, a key never goes on a command line.** Every user can read `/proc/*/cmdline`, and
  `agent` is the isolation boundary. curl takes the header on stdin:
  `curl -H @- … <<<"Authorization: Bearer $SPARK_API_KEY"`. Code reads keys from the environment.
- **127.0.0.1 is not a boundary against `agent`.** Binding to 127.0.0.1 keeps a port off the
  network, not away from the box's own users. llama-swap checks keys, but the engines it starts
  listen on 5800 and up with none, so any local user, `agent` included, can call a loaded model
  directly, around llama-swap's keys. In Phase 1 that costs nothing: `agent` has a key of its own,
  and a direct call reaches only a model that is already loaded, since loading one still takes
  llama-swap and a key. Nothing in this phase may rely on 127.0.0.1 to keep `agent` out. The gate
  (Phase 2) doesn't change this: it decides loads, not who reaches an engine. Phase 3's per-key
  allow-lists and concurrency limits don't hold against a direct call either, so that phase decides
  how to close it (plan.md, *Open items and risks*).
- **Root never writes, `chown`s or `chmod`s through a path `agent` or `spark` can change.** A link
  planted there turns a root write into a write to any file on the box. To give `agent` a file,
  root reads only what it needs and `agent` writes the file (`runuser -u agent -- …`). A download
  that root will run goes into a fresh folder from `mktemp -d`, never a fixed `/tmp` name.
- Open WebUI's `RAG_OPENAI_API_BASE_URL` and `AUDIO_STT_OPENAI_API_BASE_URL` are always set
  explicitly; unset, they fall back to OpenAI's servers.

## Review Focus

1. **A model that doesn't fit is requested** — expected: `spark launch` refuses with needed vs
   available and the reserve, `spark status` says why, and nothing else is unloaded. *(Tasks 2
   and 5.)*
2. **The brake unloads a model and the client asks again** — expected: the launch check refuses while
   the hold stands; no reload thrash. *(Tasks 2 and 4.)*
3. **llama-swap is down or the key is wrong while the brake runs** — expected: the brake logs and keeps
   watching memory; it never crashes. *(Task 4.)*
4. **A registry edit that breaks the budget, reuses an alias or loses a revision pin** — expected:
   `spark render` refuses with the reason. *(Tasks 1 and 6.)*
5. **`spark apply` while models are loaded** — expected: it shows the diff and refuses to restart
   llama-swap unless `--now`. *(Task 7.)*
6. **A routine `apt upgrade` replaces a library the engines use while models are loaded, then the
   box reboots** — expected: needrestart restarts no `local-ai-*` unit, the models stay loaded, the
   stack comes back by itself after the reboot, and `make doctor` passes. *(Tasks 10 and 16.)*
7. **Upgrade day's apt plan would leave a kernel without its NVIDIA module or change the driver
   branch, Dan answers no, or apt fails partway** — expected: `make upgrade-gpu` refuses before
   anything moves, or stops. Every way out runs the hold, and the hold after apt's move is tried
   again if a signal cuts it off. When a hold stops, or a signal cuts off a hold the way out runs,
   the retry included, the set stays released, and it says to run `make hold-gpu`. A signal there
   after apt ran also brings a warning not to reboot before the module check; a hold that stopped
   gets the way out's usual verdict instead. Whether anything moved is judged without the hold
   letter, so answering no counts as nothing moved. It suggests starting the stack only when
   nothing moved and the newest kernel still has its module, even when a hold then stopped, and
   never a reboot after a partial move. *(Task 10.)*

***

## File structure

| Path | Responsibility |
|---|---|
| `stack/models.yaml` | Budget, brake thresholds, engine paths, the four models |
| `stack/templates/*.service`, `stack/templates/compose.yaml`, `stack/templates/searxng-settings.yml` | Rendered by `spark render` |
| `spark/src/spark/paths.py` | Default paths and URLs (env-overridable) |
| `spark/src/spark/registry.py` | Load and validate `stack/models.yaml` |
| `spark/src/spark/memory.py` | `/proc/meminfo` → `MemInfo` |
| `spark/src/spark/hold.py` | The brake's hold file |
| `spark/src/spark/admission.py` | `admit()` — the launch check |
| `spark/src/spark/launch.py` | `spark launch <model> -- <cmd>` |
| `spark/src/spark/llamaswap.py` | Minimal llama-swap API client |
| `spark/src/spark/brake.py` | `plan_brake()` and the `spark brake` loop |
| `spark/src/spark/status.py` | `spark status` |
| `spark/src/spark/render.py` | `spark render` |
| `spark/src/spark/apply.py` | `spark apply` |
| `spark/src/spark/models.py` | `spark models pull` |
| `spark/src/spark/clients.py` | `spark clients pi` |
| `spark/src/spark/doctor.py` | `spark doctor` (`make doctor`) v0: Phase 0's guardrails and the stack |
| `stack/host/bootstrap.sh` (Phase 0's) | Gains the cache folders (Task 6), the needrestart step and the `--upgrade-gpu` mode (Task 10) |
| `stack/host/needrestart.conf` | needrestart leaves the `local-ai-*` units alone |
| `website/how-to/pi.md`, `website/how-to/deploy.md` | Runbooks |

***

### Task 1 [Mac]: the model registry

**Files:**

- Create: `spark/src/spark/registry.py`, `spark/tests/test_registry.py`,
  `spark/tests/fixtures/models.yaml`

**Interfaces:**

- Produces:
  - `RegistryError(ValueError)`
  - `Source(repo: str, revision: str, file: str, mmproj: str | None)`
  - `Model(name, capability, engine, source: Source, resident: bool, footprint_gib: float,
    footprint_measured: bool, ctx: int, parallel: int, cache_ram_mib: int, args: tuple[str, ...],
    roles: tuple[str, ...])`
  - `Budget(allocatable_gib: float, reserve_gib: float)`,
    `BrakeThresholds(warn_gib: float, brake_gib: float, poll_ms: int)`
  - `Registry(budget, brake, engines: dict[str, str], models: dict[str, Model])` with
    `static_total_gib() -> float`
  - `load_registry(path: Path) -> Registry`

- [ ] **Step 1: Branch and fixture**

`git switch main && git pull && git switch -c phase-1`

`spark/tests/fixtures/models.yaml` (fake repos and revisions — never real ones in fixtures):

```yaml
budget: {allocatable_gib: 102, reserve_gib: 24}
brake: {warn_gib: 28, brake_gib: 20, poll_ms: 250}
engines:
  llama.cpp: /opt/local-ai/bin/llama.cpp/b11146/llama-server
  whisper.cpp: /opt/local-ai/bin/whisper.cpp/v1.9.4/whisper-server
models:
  vision-chat:
    capability: chat
    roles: [small, vision]
    resident: true
    engine: llama.cpp
    source: {repo: example-org/vision-GGUF, revision: "1111111111111111111111111111111111111111", file: vision.gguf, mmproj: vision-mmproj.gguf}
    ctx: 32768
    parallel: 2
    cache_ram_mib: 1024
    footprint_gib: 18
    footprint_measured: false
    args: [--load-mode, none]
  embed:
    capability: embeddings
    roles: [embed]
    resident: true
    engine: llama.cpp
    source: {repo: example-org/embed-GGUF, revision: "2222222222222222222222222222222222222222", file: embed.gguf}
    ctx: 8192
    parallel: 1
    cache_ram_mib: 0
    footprint_gib: 1.5
    footprint_measured: false
    args: [--pooling, last, --ubatch-size, "8192"]
  stt:
    capability: transcription
    roles: [stt]
    resident: true
    engine: whisper.cpp
    source: {repo: example-org/whisper, revision: "3333333333333333333333333333333333333333", file: whisper.bin}
    ctx: 1
    parallel: 1
    cache_ram_mib: 0
    footprint_gib: 2.5
    footprint_measured: false
    args: [--language, auto, --convert, --tmp-dir, /var/lib/local-ai/hf/tmp]
  coder:
    capability: chat
    roles: [coder]
    resident: false
    engine: llama.cpp
    source: {repo: example-org/coder-GGUF, revision: "4444444444444444444444444444444444444444", file: coder.gguf}
    ctx: 131072
    parallel: 1
    cache_ram_mib: 2048
    footprint_gib: 28
    footprint_measured: false
    args: [--load-mode, none, --spec-type, draft-mtp, --spec-draft-n-max, "3"]
```

- [ ] **Step 2: Write the failing tests**

`spark/tests/test_registry.py`:

```python
from pathlib import Path

import pytest
import yaml

from spark.registry import RegistryError, load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"


def mutated(tmp_path, change) -> Path:
    data = yaml.safe_load(FIXTURE.read_text())
    change(data)
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_loads_the_fixture():
    registry = load_registry(FIXTURE)
    assert set(registry.models) == {"vision-chat", "embed", "stt", "coder"}
    assert registry.models["vision-chat"].source.mmproj == "vision-mmproj.gguf"
    assert registry.models["coder"].resident is False
    assert registry.static_total_gib() == 50.0


def test_revision_must_be_a_pinned_commit(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["embed"]["source"].update(revision="main"))
    with pytest.raises(RegistryError, match="revision"):
        load_registry(path)


def test_engine_must_serve_the_capability(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["stt"].update(engine="llama.cpp"))
    with pytest.raises(RegistryError, match="capability"):
        load_registry(path)


def test_roles_are_unique(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["coder"].update(roles=["small"]))
    with pytest.raises(RegistryError, match="role"):
        load_registry(path)


def test_reserve_must_exceed_the_brake(tmp_path):
    path = mutated(tmp_path, lambda d: d["budget"].update(reserve_gib=18))
    with pytest.raises(RegistryError, match="reserve"):
        load_registry(path)


def test_args_may_not_contain_whitespace_or_quotes(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["coder"].update(args=["--x", "a b"]))
    with pytest.raises(RegistryError, match="args"):
        load_registry(path)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_registry.py`
Expected: FAIL — `ModuleNotFoundError: spark.registry`.

- [ ] **Step 4: Implement**

`spark/src/spark/registry.py`:

```python
"""The model registry — stack/models.yaml, the one file to edit to add, swap or retire a model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ENGINES = {"llama.cpp": {"chat", "embeddings"}, "whisper.cpp": {"transcription"}}
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
UNSAFE_ARG = re.compile(r"[\s'\"]")


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    repo: str
    revision: str
    file: str
    mmproj: str | None = None


@dataclass(frozen=True)
class Model:
    name: str
    capability: str
    engine: str
    source: Source
    resident: bool
    footprint_gib: float
    footprint_measured: bool
    ctx: int
    parallel: int
    cache_ram_mib: int
    args: tuple[str, ...]
    roles: tuple[str, ...]


@dataclass(frozen=True)
class Budget:
    allocatable_gib: float
    reserve_gib: float


@dataclass(frozen=True)
class BrakeThresholds:
    warn_gib: float
    brake_gib: float
    poll_ms: int


@dataclass(frozen=True)
class Registry:
    budget: Budget
    brake: BrakeThresholds
    engines: dict[str, str]
    models: dict[str, Model]

    def static_total_gib(self) -> float:
        return float(sum(m.footprint_gib for m in self.models.values()))


def _model(name: str, raw: dict, engines: dict[str, str]) -> Model:
    if not NAME.match(name):
        raise RegistryError(f"{name}: names are lowercase letters, digits, '.' and '-'")
    engine = raw.get("engine")
    if engine not in engines:
        raise RegistryError(f"{name}: engine {engine!r} is not in the engines table")
    capability = raw.get("capability")
    if capability not in ENGINES.get(engine, set()):
        raise RegistryError(f"{name}: capability {capability!r} is not served by {engine}")
    src = raw.get("source") or {}
    if not REVISION.match(str(src.get("revision", ""))):
        raise RegistryError(f"{name}: source.revision must be a 40-hex commit, not a branch or tag")
    args = tuple(str(a) for a in raw.get("args") or ())
    if any(UNSAFE_ARG.search(a) for a in args):
        raise RegistryError(f"{name}: args may not contain whitespace or quotes")
    model = Model(
        name=name,
        capability=capability,
        engine=engine,
        source=Source(src["repo"], src["revision"], src["file"], src.get("mmproj")),
        resident=bool(raw.get("resident", False)),
        footprint_gib=float(raw["footprint_gib"]),
        footprint_measured=bool(raw.get("footprint_measured", False)),
        ctx=int(raw["ctx"]),
        parallel=int(raw.get("parallel", 1)),
        cache_ram_mib=int(raw.get("cache_ram_mib", 0)),
        args=args,
        roles=tuple(raw.get("roles") or ()),
    )
    if model.footprint_gib <= 0 or model.ctx <= 0 or model.parallel < 1 or model.cache_ram_mib < 0:
        raise RegistryError(f"{name}: footprint, ctx and parallel must be positive; cache_ram_mib ≥ 0")
    return model


def load_registry(path: Path) -> Registry:
    data = yaml.safe_load(Path(path).read_text()) or {}
    budget = Budget(**data["budget"])
    brake = BrakeThresholds(**data["brake"])
    if not brake.warn_gib > brake.brake_gib:
        raise RegistryError("brake: warn_gib must be above brake_gib")
    if not budget.reserve_gib > brake.brake_gib:
        raise RegistryError("budget: reserve_gib must exceed brake.brake_gib, or a fresh load trips the brake")
    engines = {str(k): str(v) for k, v in (data.get("engines") or {}).items()}
    models = {name: _model(name, raw, engines) for name, raw in (data.get("models") or {}).items()}
    seen: dict[str, str] = {}
    for model in models.values():
        for role in model.roles:
            if role in seen:
                raise RegistryError(f"role {role!r} is used by both {seen[role]} and {model.name}")
            seen[role] = model.name
    return Registry(budget, brake, engines, models)
```

- [ ] **Step 5: Run the tests — they pass**

Run: `uv run --frozen --project spark pytest spark/tests`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add spark/src/spark/registry.py spark/tests/test_registry.py spark/tests/fixtures/models.yaml
git commit -m "feat(spark): 🤖 add the model registry" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 2 [Mac]: memory, the brake's hold file, and the launch check

**Files:**

- Create: `spark/src/spark/paths.py`, `spark/src/spark/memory.py`, `spark/src/spark/hold.py`,
  `spark/src/spark/admission.py`, `spark/src/spark/launch.py`, `spark/tests/test_launch.py`
- Modify: `spark/src/spark/cli.py` (register `launch`)

**Interfaces:**

- Consumes: `load_registry`, `Model`, `Budget` (Task 1).
- Produces:
  - `paths.REGISTRY`, `paths.STATE`, `paths.LLAMASWAP_URL` (env-overridable: `SPARK_REGISTRY`,
    `SPARK_STATE`, `SPARK_LLAMASWAP_URL`)
  - `MemInfo(total_gib: float, available_gib: float)`; `parse_meminfo(text) -> MemInfo`;
    `read_meminfo(path=Path("/proc/meminfo")) -> MemInfo`
  - `Hold(since: str, reason: str, unloaded: tuple[str, ...])`; `read_hold(state_dir) -> Hold | None`;
    `write_hold(state_dir, hold) -> None` (atomic); `release_hold(state_dir) -> bool`
  - `Decision(ok: bool, reason: str)`; `admit(model, mem, budget, hold) -> Decision`
  - CLI `spark launch <model> -- <engine cmd…>`: exec on success; exit 3 refused, 2 usage error
  - `engine_env(env) -> dict[str, str]` (in `launch.py`): the environment the engine gets —
    llama-swap's, without any `LLAMASWAP_KEY_*` variable
  - `record_refusal(state_dir, model, reason) -> None`, `read_refusal(state_dir) -> dict | None`,
    `clear_refusal(state_dir) -> None` (in `launch.py`): a refusal leaves
    `{"at", "model", "reason"}` in `last-refusal.json` for `spark status` — the client itself only sees
    a failed start — and the next successful start clears it

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_launch.py`:

```python
from pathlib import Path

import pytest

from spark import launch
from spark.admission import admit
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.memory import MemInfo, parse_meminfo
from spark.registry import load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"
MEMINFO = "MemTotal:       127622144 kB\nMemFree:  1024 kB\nMemAvailable:   73400320 kB\n"


def test_parse_meminfo_in_gib():
    mem = parse_meminfo(MEMINFO)
    assert round(mem.total_gib, 1) == 121.7
    assert mem.available_gib == 70.0


def test_parse_meminfo_needs_both_fields():
    with pytest.raises(ValueError, match="MemAvailable"):
        parse_meminfo("MemTotal: 1 kB\n")


def test_admit_fits():
    reg = load_registry(FIXTURE)
    assert admit(reg.models["coder"], MemInfo(121.7, 70.0), reg.budget, None).ok


def test_admit_refuses_when_it_would_eat_the_reserve():
    reg = load_registry(FIXTURE)
    decision = admit(reg.models["coder"], MemInfo(121.7, 40.0), reg.budget, None)
    assert not decision.ok
    assert decision.reason == "needs ~28 GiB, 40 GiB available (24 GiB reserve kept)"


def test_admit_refuses_while_the_brake_holds():
    reg = load_registry(FIXTURE)
    hold = Hold(since="2026-09-23T10:00:00", reason="18.0 GiB available", unloaded=("coder",))
    decision = admit(reg.models["embed"], MemInfo(121.7, 90.0), reg.budget, hold)
    assert not decision.ok and "brake" in decision.reason and "spark brake --release" in decision.reason


def test_hold_round_trip(tmp_path):
    assert read_hold(tmp_path) is None
    write_hold(tmp_path, Hold("t", "why", ("a",)))
    assert read_hold(tmp_path) == Hold("t", "why", ("a",))
    assert release_hold(tmp_path) is True
    assert read_hold(tmp_path) is None and release_hold(tmp_path) is False


def test_launch_execs_the_engine_when_it_fits(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 70.0))
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: calls.setdefault("oom", True))
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: calls.update(file=f, argv=a))
    code = launch.main_launch(["coder", "--", "/bin/engine", "--port", "5800"], registry=FIXTURE, state=tmp_path)
    assert code == 0 and calls == {"oom": True, "file": "/bin/engine", "argv": ["/bin/engine", "--port", "5800"]}


def test_the_engine_inherits_no_api_key(tmp_path, monkeypatch):
    # llama-swap reads its keys from its environment, and every engine it starts inherits that
    # environment. Engines parse third-party model files and need none of the keys.
    calls = {}
    monkeypatch.setenv("LLAMASWAP_KEY_AGENT", "x")
    monkeypatch.setenv("LLAMASWAP_KEY_SPARK", "x")
    monkeypatch.setenv("HF_HOME", "/var/lib/local-ai/hf")
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 70.0))
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: None)
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: calls.update(env=env))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert [name for name in calls["env"] if name.startswith("LLAMASWAP_KEY_")] == []
    assert calls["env"]["HF_HOME"] == "/var/lib/local-ai/hf"


def test_launch_refuses_with_exit_3(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 30.0))
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: pytest.fail("must not exec"))
    code = launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert code == 3
    assert "spark: not starting coder: needs ~28 GiB" in capsys.readouterr().err


def test_launch_unknown_model_is_a_usage_error(tmp_path):
    assert launch.main_launch(["nope", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path) == 2


def test_a_refusal_is_kept_for_spark_status_until_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: None)
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: None)
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 30.0))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    refusal = launch.read_refusal(tmp_path)
    assert refusal["model"] == "coder" and refusal["reason"].startswith("needs ~28 GiB")
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 90.0))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert launch.read_refusal(tmp_path) is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_launch.py`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

`spark/src/spark/paths.py`:

```python
"""Default locations on brightroar; each can be overridden by an environment variable."""

import os
from pathlib import Path

REGISTRY = Path(os.environ.get("SPARK_REGISTRY", "/opt/local-ai/etc/models.yaml"))
STATE = Path(os.environ.get("SPARK_STATE", "/var/lib/local-ai/brake"))
LLAMASWAP_URL = os.environ.get("SPARK_LLAMASWAP_URL", "http://127.0.0.1:9100")
```

`spark/src/spark/memory.py`:

```python
"""Free memory as GB10 sees it: MemAvailable from /proc/meminfo (never nvidia-smi)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KIB_PER_GIB = 1024 * 1024


@dataclass(frozen=True)
class MemInfo:
    total_gib: float
    available_gib: float


def parse_meminfo(text: str) -> MemInfo:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key.strip()] = int(parts[0])
    for needed in ("MemTotal", "MemAvailable"):
        if needed not in values:
            raise ValueError(f"/proc/meminfo has no {needed}")
    return MemInfo(values["MemTotal"] / KIB_PER_GIB, values["MemAvailable"] / KIB_PER_GIB)


def read_meminfo(path: Path = Path("/proc/meminfo")) -> MemInfo:
    return parse_meminfo(Path(path).read_text())
```

`spark/src/spark/hold.py`:

```python
"""The brake's hold: while it exists, `spark launch` refuses every load. Only Dan releases it."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

FILE = "hold.json"


@dataclass(frozen=True)
class Hold:
    since: str
    reason: str
    unloaded: tuple[str, ...]


def read_hold(state_dir: Path) -> Hold | None:
    path = Path(state_dir) / FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Hold(data["since"], data["reason"], tuple(data.get("unloaded", ())))


def write_hold(state_dir: Path, hold: Hold) -> None:
    path = Path(state_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(hold) | {"unloaded": list(hold.unloaded)}))
    os.replace(tmp, path)


def release_hold(state_dir: Path) -> bool:
    path = Path(state_dir) / FILE
    if path.exists():
        path.unlink()
        return True
    return False
```

`spark/src/spark/admission.py`:

```python
"""The Phase 1 launch check: the brake's hold, then a static fit against MemAvailable − reserve."""

from __future__ import annotations

from dataclasses import dataclass

from spark.hold import Hold
from spark.memory import MemInfo
from spark.registry import Budget, Model


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str


def admit(model: Model, mem: MemInfo, budget: Budget, hold: Hold | None) -> Decision:
    if hold is not None:
        return Decision(
            False,
            f"the memory brake has held new loads since {hold.since} ({hold.reason}); "
            "run `spark brake --release` once memory is back",
        )
    if model.footprint_gib > mem.available_gib - budget.reserve_gib:
        return Decision(
            False,
            f"needs ~{model.footprint_gib:.0f} GiB, {mem.available_gib:.0f} GiB available "
            f"({budget.reserve_gib:.0f} GiB reserve kept)",
        )
    return Decision(True, "fits")
```

`spark/src/spark/launch.py`:

```python
"""`spark launch <model> -- <engine cmd…>` — llama-swap runs every engine through this.

It refuses a load while the brake holds or when the model doesn't fit, and marks the engine as the
first process the kernel or earlyoom should kill — GB10's GPU memory may not count toward
oom_score, so without this a user's job could be chosen instead. The engine gets llama-swap's
environment without its API keys: it parses third-party model files and needs none of them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from spark import paths
from spark.admission import admit
from spark.hold import read_hold
from spark.memory import read_meminfo
from spark.registry import load_registry

REFUSAL = "last-refusal.json"
KEY_PREFIX = "LLAMASWAP_KEY_"


def engine_env(env: Mapping[str, str]) -> dict[str, str]:
    """llama-swap's environment without its API keys, which every engine would otherwise inherit."""
    return {name: value for name, value in env.items() if not name.startswith(KEY_PREFIX)}


def _mark_first_to_kill() -> None:
    try:
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass  # not Linux, or not permitted — the brake still stands


def record_refusal(state: Path, model: str, reason: str) -> None:
    """Leave the reason where `spark status` shows it; the client only sees a failed start."""
    at = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        (Path(state) / REFUSAL).write_text(json.dumps({"at": at, "model": model, "reason": reason}))
    except OSError:
        pass  # the reason still reaches llama-swap through stderr


def read_refusal(state: Path) -> dict | None:
    try:
        return json.loads((Path(state) / REFUSAL).read_text())
    except (OSError, ValueError):
        return None


def clear_refusal(state: Path) -> None:
    try:
        (Path(state) / REFUSAL).unlink(missing_ok=True)
    except OSError:
        pass


def main_launch(argv: list[str], *, registry: Path = paths.REGISTRY, state: Path = paths.STATE) -> int:
    if "--" not in argv or argv.index("--") != 1 or len(argv) < 3:
        print("usage: spark launch <model> -- <engine command…>", file=sys.stderr)
        return 2
    name, cmd = argv[0], argv[2:]
    reg = load_registry(registry)
    model = reg.models.get(name)
    if model is None:
        print(f"spark: unknown model {name!r}", file=sys.stderr)
        return 2
    decision = admit(model, read_meminfo(), reg.budget, read_hold(state))
    if not decision.ok:
        print(f"spark: not starting {name}: {decision.reason}", file=sys.stderr)
        record_refusal(state, name, decision.reason)
        return 3
    clear_refusal(state)
    _mark_first_to_kill()
    os.execvpe(cmd[0], cmd, engine_env(os.environ))
    return 0  # reached only when execvpe is replaced in tests


def register(subparsers) -> None:
    p = subparsers.add_parser("launch", help="start an engine if it fits (llama-swap's cmd prefix)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=lambda args: main_launch(args.rest))
```

Register in `cli.py` beside the others: `from spark import launch` / `launch.register(subparsers)`.

- [ ] **Step 4: Run the tests — they pass**

Run: `uv run --frozen --project spark pytest spark/tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/{paths,memory,hold,admission,launch,cli}.py spark/tests/test_launch.py
git commit -m "feat(spark): 🤖 add the launch check, memory reader and brake hold" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 3 [Mac]: the llama-swap client

**Files:**

- Create: `spark/src/spark/llamaswap.py`, `spark/tests/test_llamaswap.py`

**Interfaces:**

- Produces: `Running(model: str, state: str)`; `LlamaSwapError(RuntimeError)`;
  `LlamaSwapUnreachable(LlamaSwapError)` (nothing answered — as opposed to an HTTP error such as a
  wrong key, which says nothing about what is loaded);
  `LlamaSwap(base_url: str, api_key: str | None, timeout: float = 10.0)` with
  `running() -> list[Running]` (`GET /running`) and `unload(model: str) -> None`
  (`POST /api/models/unload/{model}`); `key_from_env(name: str) -> str | None`.

- [ ] **Step 1: Write the failing tests** (a real local HTTP server, no mocks of urllib)

`spark/tests/test_llamaswap.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running

SEEN: list[tuple[str, str, str | None]] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: dict | str):
        data = body if isinstance(body, str) else json.dumps(body)
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data.encode())

    def do_GET(self):
        SEEN.append(("GET", self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer good":
            return self._reply(401, {"error": {"message": "unauthorized: invalid or missing API key"}})
        self._reply(200, {"running": [{"model": "coder", "state": "ready", "cmd": "x", "proxy": "y", "ttl": 0}]})

    def do_POST(self):
        SEEN.append(("POST", self.path, self.headers.get("Authorization")))
        if self.path.endswith("/nope"):
            return self._reply(404, {"error": {"message": "model not found"}})
        self._reply(200, "OK")


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_running_sends_the_key(server):
    assert LlamaSwap(server, "good").running() == [Running("coder", "ready")]
    assert SEEN == [("GET", "/running", "Bearer good")]


def test_wrong_key_is_a_clear_error(server):
    with pytest.raises(LlamaSwapError, match="401"):
        LlamaSwap(server, "bad").running()


def test_unload_posts_to_the_model(server):
    LlamaSwap(server, "good").unload("coder")
    assert SEEN[-1][:2] == ("POST", "/api/models/unload/coder")


def test_unload_unknown_model_raises(server):
    with pytest.raises(LlamaSwapError, match="404"):
        LlamaSwap(server, "good").unload("nope")


def test_unreachable_is_a_clear_error():
    with pytest.raises(LlamaSwapUnreachable, match="unreachable"):
        LlamaSwap("http://127.0.0.1:9", "good", timeout=0.5).running()


def test_a_wrong_key_is_not_mistaken_for_unreachable(server):
    with pytest.raises(LlamaSwapError) as caught:
        LlamaSwap(server, "bad").running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run --frozen --project spark pytest spark/tests/test_llamaswap.py`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`spark/src/spark/llamaswap.py`:

```python
"""A minimal llama-swap API client (v257): what's running, and unload one model."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class LlamaSwapError(RuntimeError):
    pass


class LlamaSwapUnreachable(LlamaSwapError):
    """Nothing answered: llama-swap is stopped (or hung), so none of its engines is serving."""


@dataclass(frozen=True)
class Running:
    model: str
    state: str


def key_from_env(name: str) -> str | None:
    return os.environ.get(name) or None


class LlamaSwap:
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, method: str, path: str) -> bytes:
        request = urllib.request.Request(self.base_url + path, method=method)
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            raise LlamaSwapError(f"llama-swap {method} {path}: HTTP {err.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            raise LlamaSwapUnreachable(f"llama-swap unreachable at {self.base_url}: {err}") from None

    def running(self) -> list[Running]:
        data = json.loads(self._call("GET", "/running"))
        return [Running(r["model"], r["state"]) for r in data.get("running", [])]

    def unload(self, model: str) -> None:
        self._call("POST", "/api/models/unload/" + urllib.parse.quote(model, safe=""))
```

- [ ] **Step 4: Run the tests — they pass.** Run: `uv run --frozen --project spark pytest spark/tests`

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/llamaswap.py spark/tests/test_llamaswap.py
git commit -m "feat(spark): 🤖 add a minimal llama-swap client" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***
### Task 4 [Mac]: the minimal brake

**Files:**

- Create: `spark/src/spark/brake.py`, `spark/tests/test_brake.py`
- Modify: `spark/src/spark/cli.py` (register `brake`)

**Interfaces:**

- Consumes: `MemInfo`, `read_meminfo` (Task 2); `Hold`, `read_hold`, `write_hold`, `release_hold`
  (Task 2); `Registry`, `BrakeThresholds` (Task 1); `LlamaSwap`, `LlamaSwapError`, `key_from_env`
  (Task 3).
- Produces: `Action(kind: str, model: str | None = None)` with kind `warn | hold | unload`;
  `plan_brake(mem, thresholds, registry, running: list[str]) -> list[Action]`;
  `run_brake(registry, client, state_dir, *, read_mem, sleep, log, now, once=False) -> None`;
  CLI `spark brake [--once] [--release] [--key-env NAME]` (default key env `SPARK_API_KEY`).

Phase 1's order is *on-demand models first, then residents, largest first* — one unload per tick,
then re-measure. The idle-first order arrives with the gate in Phase 2 (it needs in-flight data).

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_brake.py`:

```python
from pathlib import Path

from spark import brake
from spark.brake import Action, plan_brake, run_brake
from spark.hold import Hold, read_hold, write_hold
from spark.llamaswap import LlamaSwapError, Running
from spark.memory import MemInfo
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def plan(available, running):
    return plan_brake(MemInfo(121.7, available), REG.brake, REG, running)


def test_above_warn_does_nothing():
    assert plan(40, ["coder"]) == []


def test_between_warn_and_brake_warns():
    assert plan(25, ["coder"]) == [Action("warn")]


def test_below_brake_holds_and_unloads_on_demand_first():
    assert plan(18, ["vision-chat", "coder", "embed"]) == [Action("hold"), Action("unload", "coder")]


def test_then_residents_largest_first():
    assert plan(18, ["embed", "stt", "vision-chat"]) == [Action("hold"), Action("unload", "vision-chat")]


def test_nothing_running_still_holds():
    assert plan(18, []) == [Action("hold")]


class FakeClient:
    def __init__(self, running=(), fail=False):
        self._running, self.fail, self.unloaded = list(running), fail, []

    def running(self):
        if self.fail:
            raise LlamaSwapError("llama-swap unreachable at http://127.0.0.1:9100")
        return self._running

    def unload(self, model):
        self.unloaded.append(model)


def test_loop_unloads_and_records_the_hold(tmp_path):
    client, logs = FakeClient([Running("coder", "ready")]), []
    run_brake(REG, client, tmp_path, read_mem=lambda: MemInfo(121.7, 18), sleep=lambda s: None,
              log=logs.append, now=lambda: "2026-09-23T10:00:00", once=True)
    assert client.unloaded == ["coder"]
    assert read_hold(tmp_path) == Hold("2026-09-23T10:00:00", "18.0 GiB available", ("coder",))


def test_loop_survives_llama_swap_being_down(tmp_path):
    logs = []
    run_brake(REG, FakeClient(fail=True), tmp_path, read_mem=lambda: MemInfo(121.7, 18),
              sleep=lambda s: None, log=logs.append, now=lambda: "t", once=True)
    assert any("unreachable" in line for line in logs)
    assert read_hold(tmp_path) is not None


def test_release(tmp_path, capsys):
    write_hold(tmp_path, Hold("t", "r", ()))
    assert brake.release(tmp_path) == 0
    assert read_hold(tmp_path) is None and "released" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_brake.py` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`spark/src/spark/brake.py`:

```python
"""The minimal memory brake: warn, then hold new loads and unload models before the freeze band."""

from __future__ import annotations

import argparse
import datetime
import time
from dataclasses import dataclass, replace
from pathlib import Path

from spark import paths
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.llamaswap import LlamaSwap, LlamaSwapError, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.registry import BrakeThresholds, Registry, load_registry


@dataclass(frozen=True)
class Action:
    kind: str
    model: str | None = None


def plan_brake(mem: MemInfo, thresholds: BrakeThresholds, registry: Registry, running: list[str]) -> list[Action]:
    if mem.available_gib >= thresholds.warn_gib:
        return []
    if mem.available_gib >= thresholds.brake_gib:
        return [Action("warn")]

    def order(name: str) -> tuple[bool, float]:
        model = registry.models.get(name)
        return (model.resident if model else False, -(model.footprint_gib if model else 0.0))

    victims = sorted(running, key=order)
    return [Action("hold")] + ([Action("unload", victims[0])] if victims else [])


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def run_brake(registry, client, state_dir: Path, *, read_mem=read_meminfo, sleep=time.sleep,
              log=print, now=_now, once: bool = False) -> None:
    warned = False
    while True:
        mem = read_mem()
        try:
            running = [r.model for r in client.running() if r.state in ("starting", "ready")]
        except LlamaSwapError as err:
            log(f"brake: {err}")
            running = []
        actions = plan_brake(mem, registry.brake, registry, running)
        if not actions:
            warned = False
        for action in actions:
            if action.kind == "warn" and not warned:
                log(f"brake: warning — {mem.available_gib:.1f} GiB available (warns below {registry.brake.warn_gib:g})")
                warned = True
            elif action.kind == "hold" and read_hold(state_dir) is None:
                write_hold(state_dir, Hold(now(), f"{mem.available_gib:.1f} GiB available", ()))
                log("brake: holding new loads until `spark brake --release`")
            elif action.kind == "unload":
                try:
                    client.unload(action.model)
                except LlamaSwapError as err:
                    log(f"brake: {err}")
                    continue
                hold = read_hold(state_dir)
                write_hold(state_dir, replace(hold, unloaded=hold.unloaded + (action.model,)))
                log(f"brake: unloaded {action.model} at {mem.available_gib:.1f} GiB available")
        if once:
            return
        sleep(registry.brake.poll_ms / 1000)


def release(state_dir: Path) -> int:
    print("brake: hold released" if release_hold(state_dir) else "brake: no hold to release")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("brake", help="watch memory; unload models before the box freezes")
    p.add_argument("--once", action="store_true", help="one check, then exit")
    p.add_argument("--release", action="store_true", help="clear the hold so loads can start again")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.release:
        return release(paths.STATE)
    registry = load_registry(paths.REGISTRY)
    client = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env))
    run_brake(registry, client, paths.STATE, log=lambda m: print(m, flush=True), once=args.once)
    return 0
```

Register in `cli.py`: `from spark import brake` / `brake.register(subparsers)`.

- [ ] **Step 4: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`

- [ ] **Step 5: Commit** — `git add spark/src/spark/brake.py spark/src/spark/cli.py spark/tests/test_brake.py && git commit -m "feat(spark): 🤖 add the minimal memory brake" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"`

***

### Task 5 [Mac]: `spark status`

**Files:**

- Create: `spark/src/spark/status.py`, `spark/tests/test_status.py`
- Modify: `spark/src/spark/cli.py` (register `status`)

**Interfaces:**

- Consumes: Tasks 1–3; `read_refusal` (Task 2).
- Produces: `gather(mem, registry, running: list[Running] | None, hold: Hold | None,
  refusal: dict | None = None) -> dict`;
  `format_text(status: dict) -> str`; CLI `spark status [--json] [--key-env NAME]` (exit 0 always —
  it reports problems, it doesn't fail on them).

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_status.py`:

```python
from pathlib import Path

from spark.hold import Hold
from spark.llamaswap import Running
from spark.memory import MemInfo
from spark.registry import load_registry
from spark.status import format_text, gather

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_headroom_is_measured_to_the_brake():
    status = gather(MemInfo(121.7, 70.0), REG, [], None)
    assert status["memory"]["headroom_before_brake_gib"] == 50.0


def test_loaded_models_carry_their_class_and_footprint():
    status = gather(MemInfo(121.7, 70.0), REG, [Running("coder", "ready")], None)
    assert status["loaded"] == [{"model": "coder", "state": "ready", "resident": False, "footprint_gib": 28.0}]


def test_text_names_the_models_and_the_brake():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [Running("coder", "ready")], None))
    assert "70 GiB available of 122 GiB" in text and "50 GiB before the brake" in text
    assert "coder (on demand, ~28 GiB, ready)" in text and "brake    off" in text


def test_unreachable_llama_swap_is_reported_not_raised():
    assert "llama-swap unreachable" in format_text(gather(MemInfo(121.7, 70.0), REG, None, None))


def test_a_hold_is_shown_with_how_to_release():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [], Hold("t0", "18.0 GiB available", ("coder",))))
    assert "HOLDING since t0" in text and "spark brake --release" in text


def test_the_last_refused_load_is_explained():
    refusal = {"at": "t1", "model": "coder", "reason": "needs ~28 GiB, 40 GiB available (24 GiB reserve kept)"}
    text = format_text(gather(MemInfo(121.7, 40.0), REG, [], None, refusal))
    assert "refused  coder at t1: needs ~28 GiB, 40 GiB available (24 GiB reserve kept)" in text
```

- [ ] **Step 2: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_status.py` → FAIL.

- [ ] **Step 3: Implement**

`spark/src/spark/status.py`:

```python
"""`spark status` — what's loaded, how much memory is left before the brake, and the brake's state."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from spark import paths
from spark.hold import Hold, read_hold
from spark.launch import read_refusal
from spark.llamaswap import LlamaSwap, LlamaSwapError, Running, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.registry import Registry, load_registry


def gather(mem: MemInfo, registry: Registry, running: list[Running] | None, hold: Hold | None,
           refusal: dict | None = None) -> dict:
    loaded = None
    if running is not None:
        loaded = []
        for r in running:
            model = registry.models.get(r.model)
            loaded.append({
                "model": r.model,
                "state": r.state,
                "resident": model.resident if model else None,
                "footprint_gib": model.footprint_gib if model else None,
            })
    return {
        "memory": {
            "total_gib": round(mem.total_gib, 1),
            "available_gib": round(mem.available_gib, 1),
            "headroom_before_brake_gib": round(mem.available_gib - registry.brake.brake_gib, 1),
            "brake_gib": registry.brake.brake_gib,
        },
        "loaded": loaded,
        "brake": None if hold is None else asdict(hold) | {"unloaded": list(hold.unloaded)},
        "last_refusal": refusal,
    }


def format_text(status: dict) -> str:
    m = status["memory"]
    lines = [
        f"memory   {m['available_gib']:.0f} GiB available of {m['total_gib']:.0f} GiB · "
        f"{m['headroom_before_brake_gib']:.0f} GiB before the brake ({m['brake_gib']:g} GiB)"
    ]
    if status["loaded"] is None:
        lines.append("loaded   ? (llama-swap unreachable)")
    elif not status["loaded"]:
        lines.append("loaded   nothing")
    else:
        parts = []
        for x in status["loaded"]:
            kind = "resident" if x["resident"] else "on demand"
            parts.append(f"{x['model']} ({kind}, ~{x['footprint_gib']:.0f} GiB, {x['state']})")
        lines.append("loaded   " + " · ".join(parts))
    b = status["brake"]
    if b is None:
        lines.append("brake    off")
    else:
        unloaded = ", ".join(b["unloaded"]) or "nothing yet"
        lines.append(f"brake    HOLDING since {b['since']} ({b['reason']}); unloaded: {unloaded} — "
                     "`spark brake --release` to clear")
    r = status["last_refusal"]
    if r:
        lines.append(f"refused  {r['model']} at {r['at']}: {r['reason']}")
    return "\n".join(lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="what's loaded, memory headroom, the brake")
    p.add_argument("--json", action="store_true")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    registry = load_registry(paths.REGISTRY)
    try:
        running = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=3).running()
    except LlamaSwapError:
        running = None
    status = gather(read_meminfo(), registry, running, read_hold(paths.STATE), read_refusal(paths.STATE))
    print(json.dumps(status, indent=2) if args.json else format_text(status))
    return 0
```

Register in `cli.py`: `from spark import status` / `status.register(subparsers)`.

- [ ] **Step 4: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`

- [ ] **Step 5: Commit** — `git add spark/src/spark/status.py spark/src/spark/cli.py spark/tests/test_status.py && git commit -m "feat(spark): 🤖 add spark status" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"`

***
### Task 6 [Mac]: `spark render` — the real registry, templates, and the rendered config

**Before this task: Dan's decision on the unit-file model** (plan.md, *Open items and risks*). This
plan builds option 1, the accepted model: `make apply` writes the units and the Compose file as Dan,
and `make install-units` links them. Option 2, root-owned copies, changes this task's Compose unit,
Task 7's handling of a changed unit and Task 9's `make install-units`; if Dan picks it, revise those
three first.

**Files:**

- Create: `stack/models.yaml`, `stack/templates/local-ai-llama-swap.service`,
  `stack/templates/local-ai-brake.service`, `stack/templates/local-ai-compose.service`,
  `stack/templates/local-ai-pull.service`, `stack/templates/compose.yaml`,
  `stack/templates/searxng-settings.yml`, `spark/src/spark/render.py`, `spark/tests/test_render.py`,
  `spark/tests/fixtures/versions.yaml`
- Modify: `spark/src/spark/versions.py` (optional `image` field, commit pins, a required version),
  `spark/tests/test_versions.py`, `stack/versions.yaml`, `spark/src/spark/cli.py`,
  `stack/host/bootstrap.sh` (two cache folders for `spark`), `spark/tests/test_bootstrap.py`

**Interfaces:**

- Consumes: Tasks 1 and Phase 0's `load_versions`, `Component`.
- Produces: `RenderError(ValueError)`; `model_path(source, file) -> str`;
  `engine_cmd(model, registry) -> list[str]`; `llama_swap_config(registry) -> dict`;
  `render(registry, versions, registry_text: str, templates: Path = TEMPLATES) -> dict[str, str]`
  (relative path → content); CLI `spark render --out DIR [--registry P] [--versions P]`.
  Constants: `DEPLOY="/opt/local-ai"`, `HF_HOME="/var/lib/local-ai/hf"`,
  `SPARK_BIN="/opt/local-ai/app/.venv/bin/spark"`,
  `KEY_ENVS=("LLAMASWAP_KEY_DAN_MAC","LLAMASWAP_KEY_AGENT","LLAMASWAP_KEY_OPENWEBUI","LLAMASWAP_KEY_SPARK")`.
  Bootstrap gives `spark` two more folders, `/var/lib/local-ai/cache` and
  `/var/lib/local-ai/cuda-cache`, which the llama-swap and pull units name as `XDG_CACHE_HOME` and
  `CUDA_CACHE_PATH`.

- [ ] **Step 1: `versions.py` gains an optional image name, commit pins and a required version** — add
  `image: str | None = None` as the last field of `Component` and `image=raw.get("image")` in
  `load_versions`. A source build (whisper.cpp) is pinned by the commit it was built from, so widen
  the pin pattern to `PIN = re.compile(r"^(sha256:[0-9a-f]{64}|git:[0-9a-f]{40})$")`, with the error
  text `pin must be sha256:<64 hex>, git:<40 hex>, or null`, and the header comment of
  `stack/versions.yaml` gains `# git:<40 hex> — the commit a source build was built from.` Add to
  `spark/tests/test_versions.py`:

```python
def test_a_source_build_is_pinned_by_its_commit(tmp_path):
    pinned = GOOD.replace("pin: null\n    deployed: true", "pin: git:" + "a" * 40 + "\n    deployed: true", 1)
    assert load_versions(write(tmp_path, pinned))["llama-swap"].pin == "git:" + "a" * 40
```

  Every entry must also have a version. Phase 0's review found that an entry without `version:`
  makes `load_versions` fail with a bare `KeyError` from `raw["version"]`, not a `VersionsError`
  naming the component. Put this at the top of the loop in `load_versions`:

```python
        if raw.get("version") in (None, ""):
            raise VersionsError(f"{name}: version is required")
```

  and add to `spark/tests/test_versions.py` a test that removes llama-swap's `version:` line:

```python
def test_rejects_a_component_without_a_version(tmp_path):
    with pytest.raises(VersionsError, match="version"):
        load_versions(write(tmp_path, GOOD.replace("    version: v257\n", "", 1)))
```

  Update `stack/versions.yaml`:
  `llama.cpp` → `version: b11146` (v0.5.0's build; release assets are named by build); `open-webui` →
  add `image: ghcr.io/open-webui/open-webui` and
  `pin: sha256:9591b13f13843c7721c2b8eaf7382846c81b3ffe126526d1888d1fed50c6a33f` (the standard v0.11.4
  image — the slim build now needs Postgres + pgvector); `llama-swap` →
  `pin: sha256:8fb15ff81108064eaedf95af58212be9d7f43775841e1099387dfa823e5ca2b1`
  (`llama-swap_257_linux_arm64.tar.gz`); add

```yaml
  searxng:
    version: 2026.9.23-3cd69d30e
    image: docker.io/searxng/searxng
    where: [spark]
    pin: sha256:bcfaed4091d59f7ce85670bd701a2d0295872196dc19908a3965a8353b149f83
    deployed: true
    docs: https://docs.searxng.org/
    context7: null
    changelog: https://github.com/searxng/searxng/commits/master
    advisories: https://github.com/searxng/searxng/security/advisories
```

  Run `uv run --frozen --project spark spark docs stack --write` so the Stack page follows.

- [ ] **Step 2: The real registry** — `stack/models.yaml`. Footprints are estimates (file sizes plus
  context memory) until Phase 2 measures them. Resolve each repo's current commit on the Mac (public
  API, no token needed) and paste the 40-hex values:

```bash
for r in google/gemma-4-26B-A4B-it-qat-q4_0-gguf Qwen/Qwen3-Embedding-0.6B-GGUF \
         ggerganov/whisper.cpp unsloth/Qwen3.6-35B-A3B-MTP-GGUF; do
  printf '%s ' "$r"; curl -fsSL "https://huggingface.co/api/models/$r" | jq -r .sha
done
```

```yaml
# The one file to edit to add, swap or retire a model. `spark render` refuses anything invalid,
# including a model set that doesn't fit allocatable − reserve.
budget:
  allocatable_gib: 102   # CUDA-allocatable ceiling — reported, not yet measured on this box
  reserve_gib: 24        # a load must leave this much MemAvailable free
brake:
  warn_gib: 28
  brake_gib: 20
  poll_ms: 250
engines:
  llama.cpp: /opt/local-ai/bin/llama.cpp/b11146/llama-server
  whisper.cpp: /opt/local-ai/bin/whisper.cpp/v1.9.4/whisper-server
models:
  gemma-4-26b-a4b:
    capability: chat
    roles: [small, vision]
    resident: true
    engine: llama.cpp
    source:
      repo: google/gemma-4-26B-A4B-it-qat-q4_0-gguf
      revision: <paste the sha from the loop above>
      file: gemma-4-26B_q4_0-it.gguf
      mmproj: gemma-4-26B-it-mmproj.gguf
    ctx: 32768
    parallel: 2
    cache_ram_mib: 1024
    footprint_gib: 19
    footprint_measured: false
    args: [--load-mode, none]
  qwen3-embedding-0.6b:
    capability: embeddings
    roles: [embed]
    resident: true
    engine: llama.cpp
    source:
      repo: Qwen/Qwen3-Embedding-0.6B-GGUF
      revision: <paste>
      file: Qwen3-Embedding-0.6B-Q8_0.gguf
    ctx: 8192
    parallel: 1
    cache_ram_mib: 0
    footprint_gib: 1.5
    footprint_measured: false
    args: [--pooling, last, --ubatch-size, "8192"]
  whisper-large-v3-turbo:
    capability: transcription
    roles: [stt]
    resident: true
    engine: whisper.cpp
    source:
      repo: ggerganov/whisper.cpp
      revision: <paste>
      file: ggml-large-v3-turbo.bin
    ctx: 1
    parallel: 1
    cache_ram_mib: 0
    footprint_gib: 3
    footprint_measured: false
    args: [--language, auto, --convert, --tmp-dir, /var/lib/local-ai/hf/tmp, --threads, "8"]
  qwen3.6-35b-a3b:
    capability: chat
    roles: [coder]
    resident: false
    engine: llama.cpp
    source:
      repo: unsloth/Qwen3.6-35B-A3B-MTP-GGUF
      revision: <paste>
      file: Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf
    ctx: 131072
    parallel: 1
    cache_ram_mib: 2048
    footprint_gib: 29
    footprint_measured: false
    args: [--load-mode, none, --spec-type, draft-mtp, --spec-draft-n-max, "3"]
```

  (`--parallel 1` for the coder: MTP doesn't yet support more slots or a projector. `ctx: 1` is a
  placeholder the whisper engine ignores.) Every `<paste>` must be replaced before committing —
  `load_registry` rejects anything that isn't 40 hex.

- [ ] **Step 3: Templates** (`str.format` placeholders in braces; a literal brace is doubled, `{{ }}`)

`stack/templates/local-ai-llama-swap.service`:

```ini
# Rendered by `spark render` — edit stack/templates/, not this file.
[Unit]
Description=local-ai: llama-swap, the model supervisor
After=network-online.target
Wants=network-online.target

[Service]
User=spark
Group=spark
EnvironmentFile=/etc/local-ai/secrets/llama-swap.env
Environment=SPARK_REGISTRY=/opt/local-ai/etc/models.yaml
Environment=SPARK_STATE=/var/lib/local-ai/brake
Environment=HF_HOME=/var/lib/local-ai/hf
# spark's home, /var/lib/local-ai, is root's: caches go to folders bootstrap gives spark.
Environment=XDG_CACHE_HOME=/var/lib/local-ai/cache
Environment=CUDA_CACHE_PATH=/var/lib/local-ai/cuda-cache
ExecStart=/opt/local-ai/bin/llama-swap/{llama_swap_version}/llama-swap -config /opt/local-ai/etc/llama-swap.yaml -listen 127.0.0.1:9100
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

`stack/templates/local-ai-brake.service`:

```ini
# Rendered by `spark render` — edit stack/templates/, not this file.
[Unit]
Description=local-ai: the memory brake
After=local-ai-llama-swap.service

[Service]
User=spark
Group=spark
EnvironmentFile=/etc/local-ai/secrets/llama-swap.env
Environment=SPARK_REGISTRY=/opt/local-ai/etc/models.yaml
Environment=SPARK_STATE=/var/lib/local-ai/brake
ExecStart=/opt/local-ai/app/.venv/bin/spark brake --key-env LLAMASWAP_KEY_SPARK
Restart=always
RestartSec=2
OOMScoreAdjust=-900

[Install]
WantedBy=multi-user.target
```

`stack/templates/local-ai-compose.service` (runs as root — the `spark` user is not in `docker`):

```ini
# Rendered by `spark render` — edit stack/templates/, not this file.
[Unit]
Description=local-ai: web services (Open WebUI, SearXNG)
After=docker.service local-ai-llama-swap.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/local-ai/etc/compose
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
```

`stack/templates/local-ai-pull.service`:

```ini
# Rendered by `spark render` — edit stack/templates/, not this file.
[Unit]
Description=local-ai: download the registry's model files at their pinned revisions

[Service]
Type=oneshot
User=spark
Group=spark
# Optional: none of Phase 1's repos is gated. A token only matters for a gated model later.
EnvironmentFile=-/etc/local-ai/secrets/hf.env
Environment=HF_HOME=/var/lib/local-ai/hf
# spark's home, /var/lib/local-ai, is root's: caches go to folders bootstrap gives spark.
Environment=XDG_CACHE_HOME=/var/lib/local-ai/cache
Environment=CUDA_CACHE_PATH=/var/lib/local-ai/cuda-cache
Environment=HF_HUB_DISABLE_PROGRESS_BARS=1
Environment=SPARK_REGISTRY=/opt/local-ai/etc/models.yaml
ExecStart=/opt/local-ai/app/.venv/bin/spark models pull
```

`stack/templates/compose.yaml`:

```yaml
# Rendered by `spark render` — edit stack/templates/compose.yaml, not this file.
name: local-ai
services:
  open-webui:
    image: {open_webui_image}
    network_mode: host
    restart: unless-stopped
    logging: {{driver: journald}}   # `make logs s=open-webui`, no docker access needed
    env_file: [/etc/local-ai/secrets/open-webui.env]
    environment:
      HOST: 127.0.0.1
      PORT: "3000"
      ENABLE_PERSISTENT_CONFIG: "false"
      ENABLE_SIGNUP: "false"   # closed after the first account, the admin; if even that is refused, see deploy.md
      ENABLE_OLLAMA_API: "false"
      OPENAI_API_BASE_URLS: http://127.0.0.1:9100/v1
      ENABLE_DIRECT_CONNECTIONS: "false"
      ENABLE_CODE_EXECUTION: "false"
      ENABLE_CODE_INTERPRETER: "false"
      TASK_MODEL_EXTERNAL: {task_model}
      RAG_EMBEDDING_ENGINE: openai
      RAG_OPENAI_API_BASE_URL: http://127.0.0.1:9100/v1
      RAG_EMBEDDING_MODEL: {embedding_model}
      AUDIO_STT_ENGINE: openai
      AUDIO_STT_OPENAI_API_BASE_URL: http://127.0.0.1:9100/v1
      AUDIO_STT_MODEL: {stt_model}
      ENABLE_WEB_SEARCH: "true"
      WEB_SEARCH_ENGINE: searxng
      SEARXNG_QUERY_URL: http://127.0.0.1:8888/search
    volumes:
      - /var/lib/local-ai/open-webui:/app/backend/data
  searxng:
    image: {searxng_image}
    network_mode: host
    restart: unless-stopped
    logging: {{driver: journald}}
    env_file: [/etc/local-ai/secrets/searxng.env]
    environment:
      GRANIAN_HOST: 127.0.0.1
      GRANIAN_PORT: "8888"
      FORCE_OWNERSHIP: "false"
    volumes:
      - ./searxng:/etc/searxng:ro
      - /var/lib/local-ai/searxng:/var/cache/searxng
```

`stack/templates/searxng-settings.yml` (not formatted; `SEARXNG_SECRET` comes from the env file):

```yaml
use_default_settings: true
server:
  limiter: false
  public_instance: false
  image_proxy: false
search:
  formats:
    - html
    - json
```

- [ ] **Step 4: Write the failing tests**

`spark/tests/fixtures/versions.yaml` — like `stack/versions.yaml` but with only `llama-swap`
(`version: v257`, `deployed: true`, `pin: null`), `open-webui` and `searxng` (each with its `image`,
`deployed: true` and `pin: "sha256:"` followed by 64 zeros), plus the required `where`, `docs`,
`changelog` fields.

`spark/tests/test_render.py`:

```python
from pathlib import Path

import pytest
import yaml

from spark.registry import load_registry
from spark.render import SPARK_BIN, RenderError, render
from spark.versions import load_versions

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]


def rendered(registry_path=FIX / "models.yaml"):
    return render(load_registry(registry_path), load_versions(FIX / "versions.yaml"),
                  Path(registry_path).read_text(), templates=ROOT / "stack/templates")


def test_every_model_starts_through_the_launch_check():
    cfg = yaml.safe_load(rendered()["llama-swap.yaml"])
    for name, model in cfg["models"].items():
        assert model["cmd"].startswith(f"{SPARK_BIN} launch {name} -- ")
        assert "--host 127.0.0.1" in model["cmd"] and "api-key" not in model["cmd"]
        assert model["proxy"] == "http://127.0.0.1:${PORT}" and model["ttl"] == 0


def test_the_group_never_evicts_and_holds_every_model():
    cfg = yaml.safe_load(rendered()["llama-swap.yaml"])
    group = cfg["routing"]["router"]["settings"]["groups"]["stack"]
    assert cfg["routing"]["router"]["use"] == "group"
    assert (group["swap"], group["exclusive"], group["persistent"]) == (False, False, True)
    assert group["members"] == sorted(cfg["models"])
    assert cfg["captureBuffer"] == 0


def test_api_keys_are_env_references_only():
    keys = yaml.safe_load(rendered()["llama-swap.yaml"])["apiKeys"]
    assert keys and all(k.startswith("${env.LLAMASWAP_KEY_") and k.endswith("}") for k in keys)


def test_compose_binds_locally_and_pins_images():
    compose = rendered()["compose/compose.yaml"]
    for needle in ("HOST: 127.0.0.1", "RAG_OPENAI_API_BASE_URL: http://127.0.0.1:9100/v1",
                   "AUDIO_STT_OPENAI_API_BASE_URL: http://127.0.0.1:9100/v1", "GRANIAN_HOST: 127.0.0.1",
                   "TASK_MODEL_EXTERNAL: vision-chat", "RAG_EMBEDDING_MODEL: embed", "AUDIO_STT_MODEL: stt"):
        assert needle in compose
    assert compose.count("@sha256:") == 2
    assert compose.count("driver: journald") == 2  # container logs readable without docker access


def test_llama_swap_listens_on_localhost_only():
    assert "-listen 127.0.0.1:9100" in rendered()["systemd/local-ai-llama-swap.service"]


def test_engines_and_downloads_cache_in_folders_bootstrap_gives_spark():
    # /var/lib/local-ai is spark's home but root's, so spark can't write a cache under $HOME. The
    # units that run engines or pull models name the spark-owned folders bootstrap creates.
    files = rendered()
    for unit in ("local-ai-llama-swap.service", "local-ai-pull.service"):
        text = files[f"systemd/{unit}"]
        assert "Environment=XDG_CACHE_HOME=/var/lib/local-ai/cache" in text, unit
        assert "Environment=CUDA_CACHE_PATH=/var/lib/local-ai/cuda-cache" in text, unit


def test_a_set_that_breaks_the_budget_is_refused(tmp_path):
    data = yaml.safe_load((FIX / "models.yaml").read_text())
    data["models"]["coder"]["footprint_gib"] = 70
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RenderError, match="budget"):
        rendered(path)


def test_the_real_registry_renders():
    render(load_registry(ROOT / "stack/models.yaml"), load_versions(ROOT / "stack/versions.yaml"),
           (ROOT / "stack/models.yaml").read_text(), templates=ROOT / "stack/templates")
```

Add to `spark/tests/test_bootstrap.py` (Phase 0's), after
`test_root_owns_the_state_directory_so_spark_cannot_swap_what_root_creates_in_it`:

```python
def test_spark_gets_its_cache_folders_from_root():
    # spark can't write its home, so the units that run engines or pull models set XDG_CACHE_HOME
    # and CUDA_CACHE_PATH to these. Root creates them directly under its own parent, never inside a
    # folder spark owns.
    for path in ("/var/lib/local-ai/cache", "/var/lib/local-ai/cuda-cache"):
        assert "-o spark -g spark -m 0750" in install_d_line(path)
```

- [ ] **Step 5: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_render.py spark/tests/test_bootstrap.py`
  → FAIL: `render.py` doesn't exist yet, and the dry run has no `install -d` line for either cache
  folder.

- [ ] **Step 6: Implement**

In `stack/host/bootstrap.sh`'s `directories()`, the line that creates `spark`'s folders gains the
two cache folders, next to `hf`, so root makes them directly under its own `/var/lib/local-ai`:

```bash
  run install -d -o spark -g spark -m 0750 /var/lib/local-ai/hf /var/lib/local-ai/open-webui /var/lib/local-ai/searxng \
    /var/lib/local-ai/cache /var/lib/local-ai/cuda-cache
```

`spark/src/spark/render.py`:

```python
"""`spark render` — stack/models.yaml + stack/versions.yaml + stack/templates → deployable files."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from spark.registry import Model, Registry, Source, load_registry
from spark.versions import Component, load_versions

TEMPLATES = Path("stack/templates")
DEPLOY = "/opt/local-ai"
HF_HOME = "/var/lib/local-ai/hf"
SPARK_BIN = f"{DEPLOY}/app/.venv/bin/spark"
KEY_ENVS = ("LLAMASWAP_KEY_DAN_MAC", "LLAMASWAP_KEY_AGENT", "LLAMASWAP_KEY_OPENWEBUI", "LLAMASWAP_KEY_SPARK")
UNITS = ("local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service", "local-ai-pull.service")


class RenderError(ValueError):
    pass


def model_path(source: Source, file: str) -> str:
    org, name = source.repo.split("/", 1)
    return f"{HF_HOME}/hub/models--{org}--{name}/snapshots/{source.revision}/{file}"


def engine_cmd(model: Model, registry: Registry) -> list[str]:
    binary = registry.engines[model.engine]
    main = model_path(model.source, model.source.file)
    if model.engine == "whisper.cpp":
        cmd = [binary, "--host", "127.0.0.1", "--port", "${PORT}", "--model", main,
               "--inference-path", "/v1/audio/transcriptions"]
    else:
        cmd = [binary, "--host", "127.0.0.1", "--port", "${PORT}", "--model", main,
               "--ctx-size", str(model.ctx), "--parallel", str(model.parallel),
               "--gpu-layers", "all", "--cache-ram", str(model.cache_ram_mib)]
        if model.source.mmproj:
            cmd += ["--mmproj", model_path(model.source, model.source.mmproj)]
        if model.capability == "embeddings":
            cmd += ["--embedding"]
    return cmd + list(model.args)


def check_budget(registry: Registry) -> None:
    room = registry.budget.allocatable_gib - registry.budget.reserve_gib
    total = registry.static_total_gib()
    if total > room:
        raise RenderError(f"the model set needs ~{total:.0f} GiB but the budget allows {room:.0f} GiB "
                          f"(allocatable {registry.budget.allocatable_gib:g} − reserve {registry.budget.reserve_gib:g})")


def llama_swap_config(registry: Registry) -> dict:
    models = {}
    for m in registry.models.values():
        models[m.name] = {
            "cmd": " ".join([SPARK_BIN, "launch", m.name, "--", *engine_cmd(m, registry)]),
            "proxy": "http://127.0.0.1:${PORT}",
            "checkEndpoint": "/health",
            "ttl": 0,
            "aliases": list(m.roles),
        }
    return {
        "healthCheckTimeout": 600,
        "captureBuffer": 0,
        "globalTTL": 0,
        "startPort": 5800,
        "logLevel": "info",
        "apiKeys": [f"${{env.{name}}}" for name in KEY_ENVS],
        "models": models,
        "routing": {"router": {"use": "group", "settings": {"groups": {"stack": {
            "swap": False, "exclusive": False, "persistent": True, "members": sorted(models)}}}}},
    }


def _only(registry: Registry, what: str, match) -> str:
    names = [m.name for m in registry.models.values() if match(m)]
    if len(names) != 1:
        raise RenderError(f"need exactly one {what}, found {names}")
    return names[0]


def _image(c: Component) -> str:
    if not c.image or not c.pin:
        raise RenderError(f"{c.name}: image and pin are required to deploy")
    return f"{c.image}:{c.version}@{c.pin}"


def render(registry: Registry, versions: dict[str, Component], registry_text: str,
           templates: Path = TEMPLATES) -> dict[str, str]:
    check_budget(registry)
    fields = {
        "llama_swap_version": versions["llama-swap"].version,
        "open_webui_image": _image(versions["open-webui"]),
        "searxng_image": _image(versions["searxng"]),
        "task_model": _only(registry, "model with the 'small' role", lambda m: "small" in m.roles),
        "embedding_model": _only(registry, "embeddings model", lambda m: m.capability == "embeddings"),
        "stt_model": _only(registry, "transcription model", lambda m: m.capability == "transcription"),
    }
    files = {
        "llama-swap.yaml": yaml.safe_dump(llama_swap_config(registry), sort_keys=False),
        "models.yaml": registry_text,
        "compose/compose.yaml": (templates / "compose.yaml").read_text().format(**fields),
        "compose/searxng/settings.yml": (templates / "searxng-settings.yml").read_text(),
    }
    for unit in UNITS:
        files[f"systemd/{unit}"] = (templates / unit).read_text().format(**fields)
    return files


def write_tree(files: dict[str, str], out: Path) -> None:
    for rel, content in files.items():
        path = Path(out) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def register(subparsers) -> None:
    p = subparsers.add_parser("render", help="render the deployable config into a directory")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    p.add_argument("--versions", type=Path, default=Path("stack/versions.yaml"))
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    files = render(load_registry(args.registry), load_versions(args.versions), args.registry.read_text())
    write_tree(files, args.out)
    print(f"render: {len(files)} files → {args.out}")
    return 0
```

Register in `cli.py`: `from spark import render as render_cmd` / `render_cmd.register(subparsers)`.
Add to `.github/workflows/ci.yml`'s `tests` job: `- run: uv run --frozen --project spark spark render --out /tmp/rendered`.

- [ ] **Step 7: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`, and
  `make lint` (bootstrap changed).

- [ ] **Step 8: Commit** — `git add stack spark .github website/reference/stack.md && git commit -m "feat(spark): 🤖 render llama-swap, systemd and compose config from the registry" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"`

***

### Task 7 [Mac]: `spark apply` — show what changes, never stop loaded models silently

**Files:**

- Create: `spark/src/spark/apply.py`, `spark/tests/test_apply.py`
- Modify: `spark/src/spark/cli.py` (register `apply`)

**Interfaces:**

- Consumes: `render`, `write_tree`, `DEPLOY`, `KEY_ENVS` (Task 6); `LlamaSwap`, `LlamaSwapError`,
  `LlamaSwapUnreachable`, `key_from_env` (Task 3); `load_versions`, `unpinned` (Phase 0).
- Produces: `diff_tree(files, etc) -> list[str]`; `app_diff(src, app) -> list[str]`;
  `units_to_restart(changed, app_changed=False) -> list[str]`;
  `apply_files(files, etc, *, app_changes, running, now_ok, dry_run, sync_app, installed, run_cmd, log) -> int`
  — 0 applied (or nothing to do), 1 refused; `running=None` means llama-swap answered but wouldn't
  say what is loaded. CLI `spark apply [--dry-run] [--now] [--key-env NAME]`, run from the repo root.

What each change restarts:

| Changed | Restarted |
|---|---|
| `llama-swap.yaml` or its unit | llama-swap. That stops every model, so it is refused while any is loaded, or while apply can't tell, unless `--now` |
| `models.yaml` or the brake's unit | the brake (`spark launch` reads the registry fresh on every start) |
| the app (`/opt/local-ai/app`, a copy of the repo's `spark/`) | the brake, the one long-running process that imports it |
| `compose/*` or its unit | the web services |
| any unit file | `systemctl daemon-reload`, first |

A unit that isn't installed yet (the first deploy, before `make install-units`) is skipped with a
hint. A refusal changes nothing at all — not the files, not the app.

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_apply.py`:

```python
from spark.apply import app_diff, apply_files, diff_tree, units_to_restart

FILES = {"llama-swap.yaml": "a", "models.yaml": "m", "compose/compose.yaml": "c",
         "systemd/local-ai-llama-swap.service": "u"}


def seeded(etc):
    for rel, content in FILES.items():
        (etc / rel).parent.mkdir(parents=True, exist_ok=True)
        (etc / rel).write_text(content)
    return etc


def run_apply(etc, files, *, app_changes=(), running=(), now_ok=False, dry_run=False, installed=True):
    ran, logs, synced = [], [], []
    code = apply_files(files, etc, app_changes=list(app_changes),
                       running=None if running is None else list(running), now_ok=now_ok,
                       dry_run=dry_run, sync_app=lambda: synced.append(True),
                       installed=lambda unit: installed, run_cmd=ran.append, log=logs.append)
    return code, ran, logs, synced


def test_nothing_changed_does_nothing(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES)
    assert (code, ran, logs) == (0, [], ["apply: nothing to change"])


def test_compose_change_restarts_only_the_web_services(tmp_path):
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"compose/compose.yaml": "c2"})
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-compose.service"]]
    assert (tmp_path / "compose/compose.yaml").read_text() == "c2"


def test_llama_swap_change_is_refused_while_models_are_loaded(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                        app_changes=["src/spark/brake.py"], running=["qwen3.6-35b-a3b"])
    assert code == 1 and ran == [] and synced == []
    assert (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert "--now" in logs[-1] and "qwen3.6-35b-a3b" in logs[-1]


def test_not_knowing_what_is_loaded_counts_as_loaded(tmp_path):
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, running=None)
    assert code == 1


def test_now_restarts_llama_swap_anyway(tmp_path):
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                running=["qwen3.6-35b-a3b"], now_ok=True)
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]


def test_unit_change_reloads_systemd_first(tmp_path):
    _, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"systemd/local-ai-llama-swap.service": "u2"})
    assert ran == [["systemctl", "daemon-reload"], ["systemctl", "restart", "local-ai-llama-swap.service"]]


def test_an_app_change_is_synced_and_restarts_the_brake(tmp_path):
    code, ran, _, synced = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/brake.py"])
    assert code == 0 and synced == [True]
    assert ran == [["systemctl", "restart", "local-ai-brake.service"]]


def test_the_first_deploy_skips_units_not_yet_installed(tmp_path):
    code, ran, logs, _ = run_apply(tmp_path, FILES, installed=False)
    assert code == 0 and ran == [["systemctl", "daemon-reload"]]
    assert (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert any("make install-units" in line for line in logs)


def test_dry_run_changes_nothing(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"compose/compose.yaml": "c2"},
                                        app_changes=["pyproject.toml"], dry_run=True)
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "compose/compose.yaml").read_text() == "c"
    assert "local-ai-compose.service" in logs[-1]


def test_restart_mapping():
    assert units_to_restart(["models.yaml"]) == ["local-ai-brake.service"]
    assert units_to_restart(["compose/searxng/settings.yml"]) == ["local-ai-compose.service"]
    assert units_to_restart([], app_changed=True) == ["local-ai-brake.service"]


def test_a_missing_file_counts_as_changed(tmp_path):
    assert diff_tree({"new.yaml": "x"}, tmp_path) == ["new.yaml"]


def test_app_diff_skips_the_venv_and_caches(tmp_path):
    src = tmp_path / "src"
    for rel in ("pyproject.toml", "src/spark/cli.py", ".venv/bin/python", "src/spark/__pycache__/cli.pyc"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("x")
    assert app_diff(src, tmp_path / "app") == ["pyproject.toml", "src/spark/cli.py"]
```

- [ ] **Step 2: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_apply.py` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

`spark/src/spark/apply.py`:

```python
"""`spark apply` — render, validate, show what changes, deploy under /opt/local-ai, and restart only
what changed. It never restarts llama-swap, which stops every model, while models are loaded —
unless told to with --now."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from spark import paths
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, key_from_env
from spark.registry import load_registry
from spark.render import DEPLOY, KEY_ENVS, render, write_tree
from spark.versions import load_versions, unpinned

LLAMA_SWAP_UNIT = "local-ai-llama-swap.service"
BRAKE_UNIT = "local-ai-brake.service"
COMPOSE_UNIT = "local-ai-compose.service"
NOT_APP = {".venv", "__pycache__", ".pytest_cache"}


def diff_tree(files: dict[str, str], etc: Path) -> list[str]:
    """Rendered files that are missing from `etc` or differ from what is there."""
    return sorted(rel for rel, content in files.items()
                  if not (Path(etc) / rel).exists() or (Path(etc) / rel).read_text() != content)


def app_diff(src: Path, app: Path) -> list[str]:
    """Files of the repo's spark/ project that are missing from, or differ in, the deployed copy."""
    changed = []
    for f in sorted(Path(src).rglob("*")):
        rel = f.relative_to(src)
        if f.is_dir() or NOT_APP & set(rel.parts):
            continue
        dest = Path(app) / rel
        if not dest.exists() or dest.read_bytes() != f.read_bytes():
            changed.append(str(rel))
    return changed


def units_to_restart(changed: list[str], app_changed: bool = False) -> list[str]:
    units = {BRAKE_UNIT} if app_changed else set()  # the one long-running process that imports the app
    for rel in changed:
        if rel in ("llama-swap.yaml", f"systemd/{LLAMA_SWAP_UNIT}"):
            units.add(LLAMA_SWAP_UNIT)
        if rel in ("models.yaml", f"systemd/{BRAKE_UNIT}"):
            units.add(BRAKE_UNIT)
        if rel.startswith("compose/") or rel == f"systemd/{COMPOSE_UNIT}":
            units.add(COMPOSE_UNIT)
    return sorted(units)


def apply_files(files: dict[str, str], etc: Path, *, app_changes: list[str], running: list[str] | None,
                now_ok: bool, dry_run: bool, sync_app, installed, run_cmd, log) -> int:
    changed = diff_tree(files, etc)
    if not changed and not app_changes:
        log("apply: nothing to change")
        return 0
    for rel in changed:
        log(f"apply: changes etc/{rel}")
    if app_changes:
        log(f"apply: changes the app ({len(app_changes)} files)")
    restart = units_to_restart(changed, bool(app_changes))
    refusal = None
    if LLAMA_SWAP_UNIT in restart and not now_ok and running != []:
        loaded = "can't tell which models are loaded" if running is None else f"models are loaded ({', '.join(running)})"
        refusal = f"apply: {loaded}, and restarting llama-swap stops every model; re-run when idle, or with --now"
    if dry_run:
        log("apply: dry run — would restart " + (", ".join(restart) or "nothing"))
        if refusal:
            log(refusal)
        return 0
    if refusal:
        log(refusal + " (nothing was changed)")
        return 1
    if app_changes:
        sync_app()
    write_tree({rel: files[rel] for rel in changed}, etc)
    if any(rel.startswith("systemd/") for rel in changed):
        run_cmd(["systemctl", "daemon-reload"])
    for unit in restart:
        if installed(unit):
            run_cmd(["systemctl", "restart", unit])
        else:
            log(f"apply: {unit} isn't installed yet — run `make install-units` once")
    return 0


def _validate_llama_swap(config: str, binary: Path) -> bool:
    """llama-swap's own check, with placeholder keys — the real ones are in a file Dan can't read."""
    if not binary.exists():
        print(f"apply: {binary} isn't installed yet — skipping llama-swap -validate")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "llama-swap.yaml"
        cfg.write_text(config)
        env = os.environ | {name: "validate-only" for name in KEY_ENVS}
        return subprocess.run([str(binary), "-config", str(cfg), "-validate"], env=env).returncode == 0


def _sync_app(src: Path, app: Path) -> None:
    for rel in app_diff(src, app):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, app / rel)
    # UV_PYTHON_INSTALL_DIR: a Python that uv downloads lands where the spark user can read it.
    # UV_LINK_MODE=copy: the deployed venv shares no files with Dan's uv cache.
    # VIRTUAL_ENV is dropped: `make apply` runs inside `uv run`, which points it at the repo's venv.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env |= {"UV_PYTHON_INSTALL_DIR": f"{DEPLOY}/python", "UV_LINK_MODE": "copy"}
    subprocess.run(["uv", "sync", "--frozen", "--no-dev", "--project", str(app)], check=True, env=env)


def _installed(unit: str) -> bool:
    state = subprocess.run(["systemctl", "show", "--property=LoadState", "--value", unit],
                           capture_output=True, text=True).stdout.strip()
    return state == "loaded"


def register(subparsers) -> None:
    p = subparsers.add_parser("apply", help="render, validate and deploy on the Spark")
    p.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    p.add_argument("--now", action="store_true", help="restart llama-swap even though models are loaded")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    registry_path = repo / "stack/models.yaml"
    if not registry_path.exists():
        print("apply: run it from the repo root (make apply)")
        return 2
    if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        print("apply: note — this clone has uncommitted changes, and they are part of what gets deployed")
    versions = load_versions(repo / "stack/versions.yaml")
    missing = unpinned(versions)
    if missing:
        print(f"apply: no pin yet for {', '.join(missing)} — record it in stack/versions.yaml first")
        return 1
    files = render(load_registry(registry_path), versions, registry_path.read_text())
    binary = Path(f"{DEPLOY}/bin/llama-swap/{versions['llama-swap'].version}/llama-swap")
    if not _validate_llama_swap(files["llama-swap.yaml"], binary):
        print("apply: llama-swap rejected the rendered config; nothing was changed")
        return 1
    try:
        running = [r.model for r in LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=3).running()]
    except LlamaSwapUnreachable:
        running = []  # llama-swap is stopped, so restarting it stops nothing
    except LlamaSwapError as err:
        print(f"apply: {err}")
        running = None  # it answered but wouldn't say what is loaded, so assume something is
    src, app = repo / "spark", Path(f"{DEPLOY}/app")
    return apply_files(files, Path(f"{DEPLOY}/etc"), app_changes=app_diff(src, app), running=running,
                       now_ok=args.now, dry_run=args.dry_run, sync_app=lambda: _sync_app(src, app),
                       installed=_installed, run_cmd=lambda cmd: subprocess.run(cmd, check=True), log=print)
```

Register in `cli.py`: `from spark import apply` / `apply.register(subparsers)`.

- [ ] **Step 4: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`

- [ ] **Step 5: Commit** — `git add spark/src/spark/apply.py spark/src/spark/cli.py spark/tests/test_apply.py && git commit -m "feat(spark): 🤖 add spark apply" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"`

***

### Task 8 [Mac]: `spark models pull`

**Files:**

- Create: `spark/src/spark/models.py`, `spark/tests/test_models.py`
- Modify: `spark/pyproject.toml` (add `huggingface_hub>=0.34` to `dependencies`, then
  `uv lock --project spark`), `spark/uv.lock`, `spark/src/spark/cli.py`

**Interfaces:**

- Consumes: `load_registry`, `Registry` (Task 1); `HF_HOME` (Task 6).
- Produces: `pull(registry, *, download, hf_home=HF_HOME, log=print) -> int` (0, or 1 if any file
  failed); CLI `spark models pull`, run as the `spark` user by `local-ai-pull.service` (`make pull`).
  Each file lands at `{HF_HOME}/hub/models--{org}--{name}/snapshots/{revision}/{file}` — the path
  Task 6's `model_path` gives the engines.

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_models.py`:

```python
from pathlib import Path

from spark.models import pull
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_pull_fetches_every_file_at_its_pinned_revision(tmp_path):
    calls = []

    def fake(**kw):
        calls.append(kw)
        return f"/cache/{kw['filename']}"

    assert pull(REG, download=fake, hf_home=str(tmp_path), log=lambda m: None) == 0
    files = {(c["repo_id"], c["filename"], c["revision"]) for c in calls}
    assert ("example-org/vision-GGUF", "vision-mmproj.gguf", "1" * 40) in files
    assert len(calls) == 5  # four models, plus the vision model's projector
    assert all(c["cache_dir"] == f"{tmp_path}/hub" for c in calls)
    assert (tmp_path / "tmp").is_dir()  # whisper-server's --tmp-dir


def test_a_failed_file_is_reported_and_the_rest_still_download(tmp_path):
    tried, logs = [], []

    def flaky(**kw):
        tried.append(kw["filename"])
        if kw["filename"] == "embed.gguf":
            raise OSError("404 Client Error")
        return "/cache/" + kw["filename"]

    assert pull(REG, download=flaky, hf_home=str(tmp_path), log=logs.append) == 1
    assert len(tried) == 5 and any("embed.gguf: FAILED" in line for line in logs)
```

- [ ] **Step 2: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_models.py` → FAIL.

- [ ] **Step 3: Implement**

`spark/src/spark/models.py`:

```python
"""`spark models pull` — download each registry file at its pinned revision into the shared HF cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from spark import paths
from spark.registry import Registry, load_registry
from spark.render import HF_HOME


def pull(registry: Registry, *, download, hf_home: str = HF_HOME, log=print) -> int:
    Path(hf_home, "tmp").mkdir(parents=True, exist_ok=True)  # whisper-server's --tmp-dir
    failed = 0
    for model in registry.models.values():
        for file in filter(None, (model.source.file, model.source.mmproj)):
            try:
                path = download(repo_id=model.source.repo, filename=file, revision=model.source.revision,
                                cache_dir=f"{hf_home}/hub")
            except Exception as err:  # network, a gated repo, a renamed file — report it and keep going
                log(f"pull: {model.name}: {file}: FAILED — {err}")
                failed += 1
                continue
            log(f"pull: {model.name}: {file} → {path}")
    return 1 if failed else 0


def register(subparsers) -> None:
    p = subparsers.add_parser("models", help="model files")
    sub = p.add_subparsers(dest="models_command", required=True)
    sub.add_parser("pull", help="download every file at its pinned revision").set_defaults(func=run_pull)


def run_pull(args: argparse.Namespace) -> int:
    from huggingface_hub import hf_hub_download  # only this command needs it

    return pull(load_registry(paths.REGISTRY), download=hf_hub_download, log=lambda m: print(m, flush=True))
```

Register in `cli.py`: `from spark import models` / `models.register(subparsers)`.

- [ ] **Step 4: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`

- [ ] **Step 5: Commit** — `git add spark/pyproject.toml spark/uv.lock spark/src/spark/models.py spark/src/spark/cli.py spark/tests/test_models.py && git commit -m "feat(spark): 🤖 add spark models pull" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"`

***

### Task 9 [Mac]: pi's provider, the deploy targets, two runbooks

**Files:**

- Create: `spark/src/spark/clients.py`, `spark/tests/test_clients.py`, `website/how-to/deploy.md`,
  `website/how-to/pi.md` (the How-to listing picks both up)
- Modify: `spark/src/spark/cli.py`, `Makefile`, `stack/versions.yaml`, `website/reference/stack.md`
  (regenerated)

**Interfaces:**

- Consumes: `load_registry`, `Registry` (Task 1).
- Produces: `pi_provider(registry, base_url, key_env) -> dict`; `merge_pi(path, provider) -> Path`
  (returns the backup's path); CLI `spark clients pi [--write] [--base-url URL] [--key-env NAME]
  [--registry P]` (defaults `http://127.0.0.1:9100/v1`, `SPARK_API_KEY`, `stack/models.yaml`; without
  `--write` it prints the provider).

- [ ] **Step 1: Write the failing tests**

`spark/tests/test_clients.py`:

```python
import json
from pathlib import Path

from spark.clients import merge_pi, pi_provider
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_provider_lists_chat_models_by_real_name_with_an_env_key():
    p = pi_provider(REG, "http://127.0.0.1:9100/v1", "SPARK_API_KEY")
    assert p["apiKey"] == "${SPARK_API_KEY}" and p["api"] == "openai-completions"
    assert p["compat"]["supportsDeveloperRole"] is False and p["compat"]["maxTokensField"] == "max_tokens"
    by_id = {m["id"]: m for m in p["models"]}
    assert set(by_id) == {"vision-chat", "coder"}  # no embeddings or speech models in a chat picker
    assert by_id["vision-chat"]["input"] == ["text", "image"] and by_id["coder"]["input"] == ["text"]
    assert by_id["vision-chat"]["contextWindow"] == 16384  # 32768 split over 2 slots
    assert by_id["vision-chat"]["maxTokens"] == 8192 and by_id["coder"]["maxTokens"] == 32768


def test_merge_keeps_other_providers_and_backs_up(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"providers": {"other": {"x": 1}}}))
    backup = merge_pi(path, {"baseUrl": "u"})
    data = json.loads(path.read_text())
    assert data["providers"]["other"] == {"x": 1} and data["providers"]["spark"] == {"baseUrl": "u"}
    assert json.loads(backup.read_text()) == {"providers": {"other": {"x": 1}}}
```

- [ ] **Step 2: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_clients.py` → FAIL.

- [ ] **Step 3: Implement**

`spark/src/spark/clients.py`:

```python
"""Client configs rendered from the registry — real model names, keys by env reference only."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from spark.registry import Registry, load_registry

PI_MODELS = Path("~/.pi/agent/models.json").expanduser()
COMPAT = {"supportsStore": False, "supportsDeveloperRole": False, "supportsReasoningEffort": False,
          "supportsUsageInStreaming": True, "supportsStrictMode": False, "maxTokensField": "max_tokens"}


def pi_provider(registry: Registry, base_url: str, key_env: str) -> dict:
    models = []
    for m in registry.models.values():
        if m.capability != "chat":
            continue  # embeddings and speech models don't belong in a chat picker
        window = m.ctx // m.parallel  # llama-server splits its context across the slots
        models.append({"id": m.name, "name": m.name, "reasoning": True,
                       "input": ["text", "image"] if m.source.mmproj else ["text"],
                       "contextWindow": window, "maxTokens": min(32768, window // 2)})
    return {"baseUrl": base_url, "api": "openai-completions", "apiKey": "${" + key_env + "}",
            "compat": dict(COMPAT), "models": models}


def merge_pi(path: Path, provider: dict) -> Path:
    """Put `provider` under providers.spark, keep every other provider, and back up the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {}
    backup = path.with_suffix(".json.bak")
    if path.exists():
        shutil.copyfile(path, backup)
    data.setdefault("providers", {})["spark"] = provider
    path.write_text(json.dumps(data, indent=2) + "\n")
    return backup


def register(subparsers) -> None:
    p = subparsers.add_parser("clients", help="client configs")
    sub = p.add_subparsers(dest="clients_command", required=True)
    pi = sub.add_parser("pi", help="the Spark provider for pi")
    pi.add_argument("--write", action="store_true", help=f"merge it into {PI_MODELS}")
    pi.add_argument("--base-url", default="http://127.0.0.1:9100/v1")
    pi.add_argument("--key-env", default="SPARK_API_KEY")
    pi.add_argument("--registry", type=Path, default=Path("stack/models.yaml"))
    pi.set_defaults(func=run_pi)


def run_pi(args: argparse.Namespace) -> int:
    provider = pi_provider(load_registry(args.registry), args.base_url, args.key_env)
    if not args.write:
        print(json.dumps(provider, indent=2))
        return 0
    backup = merge_pi(PI_MODELS, provider)
    print(f"clients: wrote the 'spark' provider to {PI_MODELS} (the previous file is {backup})")
    return 0
```

Register in `cli.py`: `from spark import clients` / `clients.register(subparsers)`.

- [ ] **Step 4: Pin pi in `stack/versions.yaml`**, then `uv run --frozen --project spark spark docs stack --write`:

```yaml
  pi:
    version: 0.85.1   # 0.86.0–0.87.1 are reported to crash llama-server; stay below them
    where: [mac, spark]
    pin: null
    deployed: false
    docs: https://www.npmjs.com/package/@earendil-works/pi-coding-agent
    context7: null
    changelog: https://www.npmjs.com/package/@earendil-works/pi-coding-agent?activeTab=versions
    advisories: null
```

- [ ] **Step 5: Makefile targets** — add each to `.PHONY` (`apply apply-dry-run apply-now
  install-units pull status logs tunnel clients`); recipe lines start with a tab:

```make
apply: ## On the Spark: render, validate, deploy; won't restart llama-swap under loaded models
	$(SPARK) apply

apply-dry-run: ## On the Spark: show what apply would change
	$(SPARK) apply --dry-run

apply-now: ## On the Spark: apply even if restarting llama-swap stops loaded models
	$(SPARK) apply --now

install-units: ## On the Spark, once (sudo): link and enable the local-ai units
	sudo systemctl link /opt/local-ai/etc/systemd/local-ai-llama-swap.service /opt/local-ai/etc/systemd/local-ai-brake.service /opt/local-ai/etc/systemd/local-ai-compose.service /opt/local-ai/etc/systemd/local-ai-pull.service
	sudo systemctl enable local-ai-llama-swap.service local-ai-brake.service local-ai-compose.service

pull: ## On the Spark: download the model files at their pinned revisions (as the spark user)
	systemctl start local-ai-pull.service
	journalctl -u local-ai-pull.service -n 40 --no-pager

status: ## On the Spark: what's loaded, memory before the brake, the brake, the last refusal
	$(SPARK) status

logs: ## On the Spark: make logs s=llama-swap|brake|pull|compose|open-webui|searxng
	@case "$(s)" in open-webui|searxng) journalctl CONTAINER_NAME=local-ai-$(s)-1 -n 100 --no-pager ;; *) journalctl -u local-ai-$(s).service -n 100 --no-pager ;; esac

tunnel: ## On the Mac: forward the Spark's llama-swap to 127.0.0.1:9100 (Ctrl-C closes it)
	ssh -N -L 9100:127.0.0.1:9100 $${SPARK_SSH_HOST:-brightroar}

clients: ## Add the Spark provider to pi on this machine
	$(SPARK) clients pi --write
```

- [ ] **Step 6: `website/how-to/pi.md`** (front matter `title: "pi, the coding agent"`,
  `description: "pi on the Mac through an SSH tunnel, and as agent in tmux on the Spark."`):
  - **The version.** pi is pinned to 0.85.1: 0.86.0 through 0.87.1 are reported to crash
    llama-server, most likely a llama.cpp bug that their longer prompt triggers. Move up only to a
    release outside that range, and change `stack/versions.yaml` in the same commit.
  - **On the Mac.** `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.85.1`, then
    `pi --version` → `0.85.1`. `SPARK_API_KEY` must be exported by your shell (the secrets runbook put
    it in `~/.secrets`); check without showing it:
    `python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"` → `True`. `make clients`
    adds the `spark` provider to `~/.pi/agent/models.json`, keeping the previous file as
    `models.json.bak`. Run `make tunnel` in a spare terminal: nothing on the Spark listens on the LAN
    in Phase 1, so pi reaches it only through the tunnel. In pi, `/model` → a Spark model; the footer
    names the model that answers.
  - **On the Spark, as `agent`.** Node 22.19 or later (Dan installs it once).
    `npm install -g --prefix ~/.local --ignore-scripts @earendil-works/pi-coding-agent@0.85.1`. The
    agent's own key sits in its `~/.secrets`. Dan runs one command for it: root reads the key from
    the service secrets, and `agent` writes its own file, so the value is never displayed and root
    never writes in `agent`'s home. From the agent's clone:
    `uv run --frozen --project spark spark clients pi --write`. Work inside tmux: `tmux new -As work`,
    then `pi`; `Ctrl-b d` detaches, and `tmux attach -t work` picks it up after logging back in.

- [ ] **Step 7: `website/how-to/deploy.md`** (front matter `title: "Deploy the stack"`,
  `description: "The first deploy on the Spark, the web UI over tailscale serve, and every later change."`).
  Each bullet below is one section of the runbook. Its bold words are the section's heading, and
  the rest is what the section says. Write it in the runbook's own words, and copy the commands
  exactly as they are here:
  - **Before the first deploy.** Phase 0 is done (bootstrap, secrets), the engines are installed
    (the Phase 1 plan, Task 11), and `id -nG` lists `spark-admin` and `adm`. If it doesn't, the
    session predates bootstrap: log out, `tmux kill-server`, log back in. `make bootstrap` has run
    from the clone you deploy from since `stack/host/` last changed: re-run it whenever that folder
    changes. It stops a running desktop and restarts earlyoom, so run it over SSH with nothing open
    on the desktop.
  - **First deploy.** `make apply` renders into `/opt/local-ai/etc`, syncs the app into
    `/opt/local-ai/app`, and reports that the units aren't installed yet. Once: `make install-units`
    (sudo). Then `make pull` (the model files, downloaded by the `spark` user; `make logs s=pull` in
    another pane shows progress), `systemctl start local-ai-llama-swap local-ai-brake local-ai-compose`
    (no sudo: the polkit rule covers `local-ai-*`), and `make status`.
  - **The web UI's first account, straight after the first start.** From the Mac, open a tunnel in a
    spare terminal, `ssh -N -L 3000:127.0.0.1:3000 brightroar`, then open `http://127.0.0.1:3000`
    and create your account: the first one becomes the admin, and signup is otherwise closed.
    Ctrl-C closes the tunnel. Do it before anything else. Until that account exists, whoever reaches
    the page first becomes the admin: any local user on the Spark, `agent` included, and every device
    on the tailnet once the page is served there. An admin reads every chat and can add Functions,
    Python that runs inside the container as root, with host networking. That the first account can
    sign up with `ENABLE_SIGNUP` false comes from Phase 0's research and is not yet tried on this
    box.

    If the page refuses even the first signup, put the admin's email and password into
    `open-webui.env` at prompts, in `secret-files.md`'s pattern, and restart the web services.

    - The password isn't shown.
    - Both values go in as typed, since `IFS=` keeps a space at either end, and single-quoted, so
      Compose reads a `$` or a ` #` in them literally.
    - The command refuses a value with a single quote or a backslash, writes nothing, and prints
      neither value. Compose reads `\'` as an escaped quote, so a password ending in `\` would leave
      the quote open, and Compose's error would print the password into the journal.

    ```bash
    sudo bash -c 'IFS= read -rp "admin email: " e; IFS= read -rsp "admin password: " p; echo; q=$(printf "\047"); case "$e$p" in *"$q"*|*\\*) echo "no single quote or backslash in either, please: the env file quotes each value with single quotes" >&2; exit 1 ;; esac; umask 027; printf "WEBUI_ADMIN_EMAIL=%s%s%s\nWEBUI_ADMIN_PASSWORD=%s%s%s\n" "$q" "$e" "$q" "$q" "$p" "$q" >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
    systemctl restart local-ai-compose
    ```

    Log in through the tunnel with that email and password. Then remove both lines, and restart once
    more so the container no longer holds them:

    ```bash
    sudo bash -c 'umask 027 && f=/etc/local-ai/secrets/open-webui.env && grep -v -e "^WEBUI_ADMIN_EMAIL=" -e "^WEBUI_ADMIN_PASSWORD=" "$f" > "$f.new" && chgrp spark "$f.new" && mv "$f.new" "$f"'
    systemctl restart local-ai-compose
    ```
  - **The web UI.** Only once the admin exists, and keys-only SSH and the Spark's repo-only GitHub
    token (Phase 0's runbooks) are done: `sudo tailscale serve --bg --https=443
    http://127.0.0.1:3000` — it survives reboots. `tailscale serve status` shows the address; it
    names your tailnet, so read it privately. Log in with the account you made. To undo:
    `sudo tailscale serve reset`, then serve again.
  - **Every later change.** `git pull`; if it changed `stack/host/`, `make bootstrap` first. Then
    `make apply-dry-run`, `make apply`. If the llama-swap config changed while models are loaded,
    apply changes nothing and says so; run it again when they're idle, or `make apply-now` to restart
    llama-swap anyway.
  - **When something is wrong.** `make status`, then `make logs s=llama-swap` (or `brake`, `pull`,
    `compose`, `open-webui`, `searxng`). A refused load appears in `make status` as a `refused` line
    with its reason.

- [ ] **Step 8: Tests pass; the site builds; commit**

Run: `uv run --frozen --project spark pytest spark/tests && make lint docs`
Expected: all pass; the How-to listing shows the two new runbooks.

```bash
git add spark Makefile stack/versions.yaml website/reference/stack.md website/how-to
git commit -m "feat(spark): 🤖 add pi's provider config, deploy targets and runbooks" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 10 [Mac]: updates never take the stack down — needrestart, `make upgrade-gpu`, `make doctor`

plan.md's Phase 1 promises that updates never take the stack down for good. Three pieces make that
true, and Tasks 12, 13 and 16 run them on the box:

- **The needrestart override.** After every apt run, needrestart restarts the services still using
  a library the run replaced. On this box it does so automatically: Phase 0 found its restart mode
  set to `a`. llama-swap's unit kills its whole control group, so the first libc or libstdc++
  upgrade would stop every loaded model. Bootstrap installs a drop-in that leaves the `local-ai-*`
  units alone, as DGX OS does for its own dashboard.
- **`make upgrade-gpu`.** Upgrade day's GPU-set steps from `website/how-to/updates.md`, as one
  command, built on Phase 0's hold (`hold_gpu_stack` and the `--hold-gpu` mode in
  `stack/host/bootstrap.sh`).
- **`make doctor` v0.** Phase 0's guardrails and the stack's smoke checks in one pass, for after any
  update, a reboot or upgrade day. `spark doctor` proper, one check per scenario, stays in Phase 2.

**Files:**

- Create: `stack/host/needrestart.conf`, `spark/src/spark/doctor.py`, `spark/tests/test_doctor.py`
- Modify: `stack/host/bootstrap.sh` (the needrestart step and the `--upgrade-gpu` mode),
  `spark/tests/test_bootstrap.py`, `spark/src/spark/cli.py` (register `doctor`), `Makefile`,
  `website/how-to/updates.md`, `website/how-to/deploy.md`, `README.md` §Contents

**Interfaces:**

- Consumes: Phase 0's `stack/host/bootstrap.sh` (`gpu_hold_patterns`, `hold_gpu_stack`, `run`,
  `say`, the `--hold-gpu` mode) and `spark/tests/test_bootstrap.py` (`INSTALLED`, `GPU_SET`,
  `gpu_env`, `script`, `held`, `dry_run`, `install_d_line`, `NO_PACKAGES`); `load_registry`,
  `Registry` (Task 1) and Task 1's `spark/tests/fixtures/models.yaml`, whose embeddings model the
  doctor tests expect to be named `embed`; `paths.LLAMASWAP_URL`, `paths.REGISTRY` (Task 2);
  `key_from_env` (Task 3).
- Produces:
  - `/etc/needrestart/conf.d/local-ai.conf`, which bootstrap installs: needrestart never restarts
    a `local-ai-*` unit.
  - `bash stack/host/bootstrap.sh --upgrade-gpu [--dry-run]`. Its exit statuses:

    | Status | When |
    |---|---|
    | 0 | apt's move is done and the newest kernel has an NVIDIA module |
    | 1 | it refused apt's plan, Dan answered no, the hold stopped, or the newest kernel has no module |
    | apt's own (100) | an apt step failed, and the hold on the way out then worked |
    | 130 | a signal cut off a hold the way out runs (its first, or the retry), whatever the signal |
    | 129, 130, 143 | HUP, INT or TERM anywhere else, and the hold on the way out then worked |
    | 2 | a usage error |

    Every way out after the release runs the hold. The hold that follows apt's move is the only
    one tried twice: cut off by a signal, the way out runs it again. The set stays released in
    three cases, and each time it says to run `make hold-gpu`:

    - the hold stops, because a package dpkg didn't finish, a hold that didn't take, no kernel in
      the set, or nothing matching the patterns stops it;
    - a signal cuts off the way out's own hold, the first hold after a refused plan, a "no" or a
      failed apt step;
    - the retried hold is cut off too.

    What it says about a reboot depends on how the hold ended. A signal, once apt has started,
    brings a warning not to reboot before step 5's module check passes. A hold that stopped gets
    the way out's usual verdict, below: after a "no", nothing moved, so that is the start hint.

    On a way out after apt started, it offers to start the stack again only if the set is as it
    was and the newest kernel still has its NVIDIA module. apt can install a new kernel, which
    isn't in the set, and then fail. Otherwise it sends Dan to *If it goes wrong*.

    The newest kernel is the one the next boot starts only while GRUB boots it: entry 0 in
    `grub.cfg` is that kernel, GRUB starts entry 0, and no `next_entry` with a value picks another.
    `/etc/default/grub` alone can't show that: `GRUB_FLAVOUR_ORDER`, `GRUB_TOP_LEVEL` and indented
    or exported settings all change it. So updates.md's GRUB check reads what GRUB will do: entry
    0's first `linux` line in `grub.cfg`, its `set default=` lines, and `grub-editenv list`. The
    script reads none of them. Dan runs that check before `make upgrade-gpu` and again before
    `sudo reboot`, and Task 12 Step 1 runs it on this box. See the open item below.

    Make targets: `upgrade-gpu` (refuses outside tmux, then runs it under sudo) and
    `upgrade-gpu-dry-run`.
  - `Check(name, ok, detail)`; `Probe(repo)` with `run`, `read`, `owner`, `listable` and `http`;
    `judge_gpu_set(code, out, err) -> Check`; `earlyoom_args(text) -> list[str]`;
    `checks(probe, key, registry) -> list[Check]`; `report(results) -> str`; CLI
    `spark doctor [--key-env NAME]`: exit 0 when every check passes, 1 when any fails, 2 when it
    isn't run from the repo root. Make target `doctor`.

What `make upgrade-gpu` does, in order:

| Step | What it does | If it refuses or fails |
|---|---|---|
| release | `apt-mark unhold` the GPU set's held members, found with the hold's own patterns, so a package held for another reason stays held | — |
| finish, refresh | `dpkg --configure -a`, `apt-get update` | it holds the set again |
| read the plan | `apt-get -s dist-upgrade` (apt-get's name for `full-upgrade`): refused if it removes a `linux-modules-nvidia-*-nvidia-hwe-*` metapackage, or installs a `linux-image-<version>` with no `linux-modules-nvidia-*` ending in `<version>`. A metapackage swapped for another driver branch's (580 for 590, say) is refused too: that move is planned and made by hand | nothing has moved; it holds the set again |
| stop the GPU's users | `systemctl stop` llama-swap and the brake, if they run; the reboot starts them | — |
| move | `apt-get dist-upgrade`: Dan reads apt's plan and answers | it holds the set again, then compares the set with how it stood before apt ran: each package's state and version, without the hold letter, which the release and the hold flip between `i` and `h`. It also checks the newest kernel for its module, since apt may have installed one outside the set. Nothing changed and the module is there (Dan answered no, or apt failed first, even if the hold then stopped): it says how to start the stack. Otherwise, even for one version: it sends Dan to *If it goes wrong* in `updates.md`, with no reboot hint |
| hold | the hold and nothing else (`hold_gpu_stack`); this hold, after apt's move, is tried once more on the way out if a signal cuts it off | it says what the hold said, and the set stays released until `make hold-gpu`. A signal during the way out's own hold, or during the retry, also leaves it released: it says so, and after apt ran, not to reboot before step 5's module check |
| check | `modinfo -k` on the newest kernel, which the next boot starts only if GRUB boots it. It doesn't check that: updates.md's GRUB check, run before `make upgrade-gpu` and again before the reboot, does (the open item below) | `DON'T REBOOT`, and *If it goes wrong* in `updates.md` |
| end | `ready: <kernel>, the newest kernel, has NVIDIA driver <version>`, then `now: sudo reboot, then make doctor` | — |

Two things it relies on are not yet checked on this box, and Task 12 Step 1 lists both. One is
the name `linux-image-<version>`, with a digit first, for a new kernel. If DGX OS names its kernels
otherwise, the plan check misses a new kernel, and only the newest-kernel check before the reboot
catches it. The other is GRUB booting the newest kernel: entry 0 in `grub.cfg` is that kernel, and
GRUB starts entry 0.

**Open item, 2026-09-25: decide before Task 10 is built.** For Phase 1's pre-flight, recommended
by the Task 12 polish review: `make upgrade-gpu` should run the GRUB check itself, after the move
and before it prints `now: sudo reboot`. It would read grubenv's `next_entry` and `saved_entry`,
their non-empty values only; `grub.cfg`'s `set default=`; and entry 0's first `linux` line, against
`newest_kernel`. On a mismatch it prints `DON'T REBOOT — GRUB boots X, not Y` and exits 1.
Stand-in paths that an environment variable overrides make it testable. This plan doesn't build it
yet. Until the decision lands, Dan re-runs updates.md's GRUB check after `make upgrade-gpu` and
before `sudo reboot`.

- [ ] **Step 1: Write the failing tests for bootstrap**

In `spark/tests/test_bootstrap.py`, the stand-ins behave more like the real tools.

- dpkg-query honours `-f`, and the fixture carries versions.
- apt-mark's hold and unhold flip the fixture's first status letter (`ii` ↔ `hi`), as the real
  ones do. It also handles `unhold`, logs to `$CALLS` when a test sets it, and can cut a hold off as
  Ctrl-C or TERM would.

Replace `FAKE_DPKG_QUERY`, `FAKE_APT_MARK` and `gpu_env` with:

```python
FAKE_DPKG_QUERY = """#!/usr/bin/env bash
# Stands in for dpkg-query -W -f=FORMAT PATTERN...: prints each fixture package that matches a
# pattern, in FORMAT: ${db:Status-Abbrev} is the fixture's two status letters and a space,
# ${Package} the name and ${Version} the version. It exits 1 if any pattern matched nothing, as
# dpkg-query does.
status=0
format='${db:Status-Abbrev}\\t${Package}\\n'
abbrev='${db:Status-Abbrev}' package='${Package}' version='${Version}'
for arg in "$@"; do
  case "$arg" in -f=*) format="${arg#-f=}" ;; esac
done
for pat in "$@"; do
  case "$pat" in -*) continue ;; esac
  found=0
  while IFS=$'\\t' read -r st pkg ver; do
    if [[ $pkg == $pat ]]; then
      line=${format//"$abbrev"/"$st "}
      line=${line//"$package"/$pkg}
      line=${line//"$version"/$ver}
      printf '%b' "$line"
      found=1
    fi
  done < "$DPKG_FIXTURE"
  (( found )) || status=1
done
exit "$status"
"""

FAKE_APT_MARK = """#!/usr/bin/env bash
# Stands in for apt-mark. `hold PKG...` records each package in $APT_MARK_HELD, except any named in
# $APT_MARK_IGNORES (a hold that silently didn't take), and turns its first status letter in
# $DPKG_FIXTURE to h, as a real hold does; `unhold` turns it back to i. `showhold` lists what was
# recorded. `hold` and `unhold` are also logged in $CALLS, when a test sets it. $APT_MARK_CUT names a
# file holding how many holds to cut off, as Ctrl-C or TERM would: the shell that ran apt-mark and
# the script above it get TERM, and nothing is held.
mark() {  # mark LETTER PKG...: set each package's first status letter in the fixture
  local letter="$1" st pkg ver
  shift
  while IFS=$'\\t' read -r st pkg ver; do
    if [[ " $* " == *" $pkg "* ]]; then st="$letter${st:1}"; fi
    printf '%s\\t%s\\t%s\\n' "$st" "$pkg" "$ver"
  done < "$DPKG_FIXTURE" > "$DPKG_FIXTURE.new"
  mv "$DPKG_FIXTURE.new" "$DPKG_FIXTURE"
}
case "$1" in
  hold)
    [[ -z "${CALLS:-}" ]] || echo "apt-mark $*" >> "$CALLS"
    if [[ -s "${APT_MARK_CUT:-}" ]] && (( $(cat "$APT_MARK_CUT") > 0 )); then
      echo $(( $(cat "$APT_MARK_CUT") - 1 )) > "$APT_MARK_CUT"
      script="$(ps -o ppid= -p "$PPID" | tr -d ' ')"
      kill -TERM "$PPID" "$script"
      exit 143
    fi
    shift
    took=()
    for pkg in "$@"; do
      [[ " ${APT_MARK_IGNORES:-} " == *" $pkg "* ]] && continue
      printf '%s\\n' "$pkg" >> "$APT_MARK_HELD"
      took+=("$pkg")
    done
    mark h "${took[@]}"
    ;;
  unhold)
    echo "apt-mark $*" >> "$CALLS"
    shift
    mark i "$@"
    ;;
  showhold) if [[ -f "$APT_MARK_HELD" ]]; then sort -u "$APT_MARK_HELD"; fi ;;
  *) echo "fake apt-mark: unexpected: $*" >&2; exit 1 ;;
esac
"""


def dpkg_lines(installed: dict[str, str], versions: dict[str, str] | None = None) -> str:
    """The fixture as the stand-ins read it: status, package and version (1.0 unless given)."""
    return "".join(f"{status}\t{pkg}\t{(versions or {}).get(pkg, '1.0')}\n" for pkg, status in installed.items())


def gpu_env(tmp_path: Path, installed: dict[str, str]) -> dict[str, str]:
    """An environment whose dpkg-query reports `installed` (package → status) and whose apt-mark
    only writes to files, so even the real (not dry-run) hold changes nothing on this machine."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("dpkg-query", FAKE_DPKG_QUERY), ("apt-mark", FAKE_APT_MARK)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    fixture = tmp_path / "installed.tsv"
    fixture.write_text(dpkg_lines(installed))
    return {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DPKG_FIXTURE": str(fixture),
        "APT_MARK_HELD": str(tmp_path / "held"),
    }
```

Then add, after `test_a_default_dry_run_never_reads_the_hosts_own_packages`:

```python
# Upgrade day. The box before it: the set held (hi), and one package held for another reason.
HELD_BEFORE = {**{pkg: "hi" if pkg in GPU_SET else status for pkg, status in INSTALLED.items()},
               "docker-ce": "hi"}
OLD, NEW = "7.0.0-1019-nvidia", "7.0.0-1020-nvidia"
# The set released (ii), as apt-mark unhold leaves it.
RELEASED = {pkg: "ii" if pkg in GPU_SET else status for pkg, status in HELD_BEFORE.items()}
# After apt moved the set: released, the new kernel's modules in, the old kernel's removed.
AFTER = {**RELEASED, f"linux-modules-nvidia-580-open-{NEW}": "ii", f"linux-modules-nvidia-580-open-{OLD}": "rc"}
NEW_SET = GPU_SET - {f"linux-modules-nvidia-580-open-{OLD}"} | {f"linux-modules-nvidia-580-open-{NEW}"}

# `apt-get -s dist-upgrade` for a set that moves cleanly: a new kernel with its modules.
GOOD_PLAN = f"""NOTE: This is only a simulation!
Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-580-open-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-image-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-580-open-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
Remv linux-modules-nvidia-580-open-{OLD} [7.0.0-1019.19]
Conf linux-image-{NEW} (7.0.0-1020.20 example [arm64])
"""
# The driver moves before its modules for the new kernel exist: apt drops the modules metapackage.
PLAN_WITHOUT_THE_METAPACKAGE = f"""Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-image-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
Inst nvidia-driver-580-open [580.178-0ubuntu1] (580.200-0ubuntu1 example [arm64])
Remv linux-modules-nvidia-580-open-nvidia-hwe-24.04 [7.0.0-1019.19]
Remv linux-modules-nvidia-580-open-{OLD} [7.0.0-1019.19]
"""
# A new kernel whose modules aren't published yet.
PLAN_WITHOUT_MODULES = f"""Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-image-nvidia-hwe-24.04 [7.0.0-1019.19] (7.0.0-1020.20 example [arm64])
"""
# A new driver branch: the 580 metapackage and driver go, the 590 ones come.
PLAN_NEW_DRIVER_BRANCH = f"""Inst linux-image-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-590-open-{NEW} (7.0.0-1020.20 example [arm64])
Inst linux-modules-nvidia-590-open-nvidia-hwe-24.04 (7.0.0-1020.20 example [arm64])
Inst nvidia-driver-590-open (590.40-0ubuntu1 example [arm64])
Remv linux-modules-nvidia-580-open-nvidia-hwe-24.04 [7.0.0-1019.19]
Remv nvidia-driver-580-open [580.178-0ubuntu1]
"""

FAKE_APT_GET = """#!/usr/bin/env bash
# Stands in for apt-get: `-s dist-upgrade` prints $APT_PLAN. The real `dist-upgrade` answers as
# $APT_ANSWER says: yes, and the installed packages become $DPKG_AFTER; no, and it aborts, changing
# nothing; partial, and it fails after the packages became $DPKG_AFTER. Every call is logged.
echo "apt-get $*" >> "$CALLS"
case "$*" in
  update) ;;
  "-s dist-upgrade") cat "$APT_PLAN" ;;
  dist-upgrade)
    case "$APT_ANSWER" in
      no) echo "Abort."; exit 1 ;;
      partial) cp "$DPKG_AFTER" "$DPKG_FIXTURE"; echo "E: Sub-process /usr/bin/dpkg returned an error code (1)" >&2; exit 100 ;;
      *) cp "$DPKG_AFTER" "$DPKG_FIXTURE" ;;
    esac
    ;;
  *) echo "fake apt-get: unexpected: $*" >&2; exit 1 ;;
esac
"""

FAKE_DPKG = """#!/usr/bin/env bash
echo "dpkg $*" >> "$CALLS"
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# Stands in for systemctl: the units in $ACTIVE_UNITS are active, and `stop` is logged.
case "$1" in
  is-active) for unit; do :; done; [[ " $ACTIVE_UNITS " == *" $unit "* ]] ;;
  stop) echo "systemctl $*" >> "$CALLS" ;;
  *) echo "fake systemctl: unexpected: $*" >&2; exit 1 ;;
esac
"""

FAKE_LINUX_VERSION = """#!/usr/bin/env bash
# Stands in for linux-version: `list` prints $KERNELS, and `sort --reverse` puts the newest first
# (the test kernels sort by name).
case "$1" in
  list) printf '%s\\n' $KERNELS ;;
  sort) sort -r ;;
esac
"""

FAKE_MODINFO = """#!/usr/bin/env bash
# Stands in for `modinfo -k KERNEL -F version nvidia`: only the kernels in $MODULE_KERNELS have one.
if [[ " $MODULE_KERNELS " == *" $2 "* ]]; then echo 580.200; else echo "modinfo: ERROR: Module nvidia not found." >&2; exit 1; fi
"""


def upgrade_env(tmp_path: Path, plan: str, *, answer: str = "yes", after: dict[str, str] = AFTER,
                after_versions: dict[str, str] | None = None, module_kernels: str = f"{OLD} {NEW}",
                cut_holds: int = 0) -> dict[str, str]:
    """gpu_env, plus stand-ins for everything upgrade day runs. Nothing touches this machine: the
    fakes only log to tmp_path/calls."""
    env = gpu_env(tmp_path, HELD_BEFORE)
    for name, text in (("apt-get", FAKE_APT_GET), ("dpkg", FAKE_DPKG), ("systemctl", FAKE_SYSTEMCTL),
                       ("linux-version", FAKE_LINUX_VERSION), ("modinfo", FAKE_MODINFO)):
        (tmp_path / "bin" / name).write_text(text)
        (tmp_path / "bin" / name).chmod(0o755)
    (tmp_path / "plan").write_text(plan)
    (tmp_path / "after.tsv").write_text(dpkg_lines(after, after_versions))
    (tmp_path / "cut").write_text(str(cut_holds))
    return {**env, "CALLS": str(tmp_path / "calls"), "APT_PLAN": str(tmp_path / "plan"),
            "DPKG_AFTER": str(tmp_path / "after.tsv"), "APT_ANSWER": answer,
            "ACTIVE_UNITS": "local-ai-llama-swap.service local-ai-brake.service",
            "KERNELS": f"{OLD} {NEW}", "MODULE_KERNELS": module_kernels, "APT_MARK_CUT": str(tmp_path / "cut")}


def real_upgrade(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Upgrade day for real, not its dry run, against upgrade_env's fakes (see real_hold)."""
    return subprocess.run(
        ["bash", "-c", 'source "$1" --dry-run && DRY_RUN=0 && upgrade_gpu', "bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def sorted_set(packages: set[str]) -> str:
    return " ".join(sorted(packages))


START_THE_STACK = "systemctl start local-ai-llama-swap.service local-ai-brake.service"
RECOVERY = "'If it goes wrong' in website/how-to/updates.md"


def test_upgrade_day_moves_the_set_and_holds_it_again(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN))
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == [
        f"apt-mark unhold {sorted_set(GPU_SET)}",  # the set, not docker-ce, held for its own reasons
        "dpkg --configure -a",
        "apt-get update",
        "apt-get -s dist-upgrade",
        "systemctl stop local-ai-llama-swap.service",
        "systemctl stop local-ai-brake.service",
        "apt-get dist-upgrade",
        f"apt-mark hold {sorted_set(NEW_SET)}",
    ]
    assert held(tmp_path) == NEW_SET
    assert f"==> ready: {NEW}, the newest kernel, has NVIDIA driver 580.200" in result.stdout
    assert "sudo reboot, then make doctor" in result.stdout


@pytest.mark.parametrize("plan, reason", [
    (PLAN_WITHOUT_THE_METAPACKAGE, "it removes linux-modules-nvidia-580-open-nvidia-hwe-24.04"),
    (PLAN_WITHOUT_MODULES, f"it installs the kernel {NEW} with no NVIDIA modules for it"),
], ids=["removes-the-modules-metapackage", "kernel-without-modules"])
def test_a_plan_that_leaves_a_kernel_without_its_module_is_refused_before_anything_moves(tmp_path, plan, reason):
    result = real_upgrade(upgrade_env(tmp_path, plan))
    assert result.returncode == 1
    assert reason in result.stderr and "nothing was installed or removed" in result.stderr
    assert "try again next upgrade day" in result.stderr
    assert "apt-get dist-upgrade" not in calls(tmp_path)  # only the simulation ran
    assert not any(line.startswith("systemctl stop") for line in calls(tmp_path))
    assert calls(tmp_path)[-1] == f"apt-mark hold {sorted_set(GPU_SET)}"  # held again on the way out
    assert held(tmp_path) == GPU_SET


def test_a_new_driver_branch_is_refused_as_a_move_made_by_hand(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, PLAN_NEW_DRIVER_BRANCH))
    assert result.returncode == 1
    assert ("the driver branch changes: it removes linux-modules-nvidia-580-open-nvidia-hwe-24.04 "
            "and installs linux-modules-nvidia-590-open-nvidia-hwe-24.04") in result.stderr
    assert "planned and made by hand" in result.stderr
    assert "try again next upgrade day" not in result.stderr
    assert "apt-get dist-upgrade" not in calls(tmp_path)
    assert held(tmp_path) == GPU_SET


def test_answering_no_holds_the_set_again_and_says_how_to_start_the_stack(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="no"))
    assert result.returncode == 1
    assert calls(tmp_path)[-2:] == ["apt-get dist-upgrade", f"apt-mark hold {sorted_set(GPU_SET)}"]
    assert held(tmp_path) == GPU_SET
    # The hold letter went from h to i and back, and nothing else changed: nothing moved, so
    # starting the stack again is safe.
    assert START_THE_STACK in result.stderr and RECOVERY not in result.stderr


def test_a_no_whose_hold_stops_still_says_how_to_start_the_stack(tmp_path):
    # Dan answers no, and the way out's hold then stops on a hold that didn't take. Nothing moved,
    # so the verdict is still the start hint, with no warning against a reboot; the hold's own
    # message says what is left to do.
    env = {**upgrade_env(tmp_path, GOOD_PLAN, answer="no"), "APT_MARK_IGNORES": "linux-image-nvidia-hwe-24.04"}
    result = real_upgrade(env)
    assert result.returncode == 1
    assert "did not hold" in result.stderr and "then run make hold-gpu" in result.stderr
    assert held(tmp_path) == GPU_SET - {"linux-image-nvidia-hwe-24.04"}
    assert START_THE_STACK in result.stderr
    assert "reboot" not in result.stderr and RECOVERY not in result.stderr


def test_a_kernel_without_a_module_stops_the_reboot(tmp_path):
    # apt reported success, but the newest kernel, which GRUB boots next by default, has no NVIDIA
    # module.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, module_kernels=OLD))
    assert result.returncode == 1
    assert f"DON'T REBOOT — {NEW}" in result.stderr
    assert held(tmp_path) == NEW_SET  # held before the check
    # The set moved: no hint to reboot or start the stack, only the way to the recovery.
    assert "sudo reboot" not in result.stdout and "systemctl start" not in result.stderr
    assert RECOVERY in result.stderr


def test_a_move_that_fails_partway_points_at_the_recovery(tmp_path):
    # apt stops partway: the driver is unpacked but not configured, so the hold can't hold it.
    half_moved = {**AFTER, "nvidia-driver-580-open": "iU"}
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="partial", after=half_moved))
    assert result.returncode == 1
    assert "nvidia-driver-580-open (iU)" in result.stderr and "run make hold-gpu" in result.stderr
    assert "systemctl start" not in result.stderr and RECOVERY in result.stderr


def test_a_move_that_only_changes_versions_counts_as_moved(tmp_path):
    # The same packages in the same states, the driver at a new version, and then apt fails: the set
    # moved, so there is no hint to start the stack or reboot.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="partial", after=RELEASED,
                                      after_versions={"nvidia-driver-580-open": "580.200"}))
    assert result.returncode == 100  # apt's own status: the hold on the way out worked
    assert held(tmp_path) == GPU_SET
    assert "systemctl start" not in result.stderr and RECOVERY in result.stderr


def test_a_new_kernel_without_its_module_gets_no_start_hint(tmp_path):
    # apt installs a new kernel, which isn't in the set, and fails before its NVIDIA modules come:
    # the set looks as it was, but the next boot would start a kernel without a module.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="partial", after=RELEASED, module_kernels=OLD))
    assert result.returncode == 100
    assert held(tmp_path) == GPU_SET
    assert "the newest kernel has no NVIDIA module" in result.stderr and RECOVERY in result.stderr
    assert "systemctl start" not in result.stderr


def test_a_cut_off_hold_is_tried_again_on_the_way_out(tmp_path):
    # Ctrl-C or TERM while the set is being held again must not leave it released.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, cut_holds=1))
    assert result.returncode == 143
    assert "holding the GPU set again" in result.stderr
    assert [line for line in calls(tmp_path) if line.startswith("apt-mark hold")] == [
        f"apt-mark hold {sorted_set(NEW_SET)}"] * 2
    assert held(tmp_path) == NEW_SET


def test_a_hold_cut_off_twice_says_what_is_left_to_do(tmp_path):
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, cut_holds=2))
    assert result.returncode == 130
    assert "stopped while holding the GPU set again — run make hold-gpu" in result.stderr
    assert "don't reboot until step 5's module check passes" in result.stderr  # apt had run
    assert held(tmp_path) == set()


def test_a_signal_during_the_way_outs_own_hold_leaves_the_set_released(tmp_path):
    # After a "no", the way out's hold is the first one, and it isn't tried again: one signal there
    # leaves the set released, and says what to do.
    result = real_upgrade(upgrade_env(tmp_path, GOOD_PLAN, answer="no", cut_holds=1))
    assert result.returncode == 130
    assert "stopped while holding the GPU set again — run make hold-gpu" in result.stderr
    assert held(tmp_path) == set()


def test_upgrade_gpu_dry_run_only_prints_the_steps(tmp_path):
    result = script("--upgrade-gpu", "--dry-run", env=upgrade_env(tmp_path, GOOD_PLAN))
    assert result.returncode == 0, result.stderr
    commands = [line.split("   (")[0] for line in result.stdout.splitlines() if line.startswith("+ ")]
    assert commands == [
        f"+ apt-mark unhold {sorted_set(GPU_SET)}",
        "+ dpkg --configure -a",
        "+ apt-get update",
        "+ apt-get -s dist-upgrade",
        "+ systemctl stop local-ai-llama-swap.service",
        "+ systemctl stop local-ai-brake.service",
        "+ apt-get dist-upgrade",
        f"+ apt-mark hold {sorted_set(GPU_SET)}",
        "+ modinfo -k <the newest kernel> -F version nvidia",
    ]
    assert calls(tmp_path) == []  # no stand-in was asked to change anything


def test_make_upgrade_gpu_refuses_to_start_outside_tmux(tmp_path):
    # A dropped SSH session in the middle of apt is the likeliest way to half-move the set.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sudo").write_text('#!/usr/bin/env bash\necho "sudo $*" >> "$CALLS"\nexit 1\n')
    (bindir / "sudo").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "TMUX": "", "CALLS": str(tmp_path / "calls")}
    result = subprocess.run(["make", "-s", "-C", str(ROOT), "upgrade-gpu"], capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "tmux new -As upgrade" in result.stdout + result.stderr
    assert calls(tmp_path) == []  # sudo was never reached
    # -s: GNU make 4 prints "Entering directory" lines under -C otherwise.
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "upgrade-gpu"],
                            capture_output=True, text=True, check=True)
    assert "sudo bash stack/host/bootstrap.sh --upgrade-gpu" in recipe.stdout.splitlines()
    phony = next(line for line in (ROOT / "Makefile").read_text().splitlines() if line.startswith(".PHONY:"))
    assert {"upgrade-gpu", "upgrade-gpu-dry-run"} <= set(phony.split()[1:])


def test_hold_gpu_and_upgrade_gpu_are_separate_modes():
    # --dry-run first, so a broken guard would only print a plan.
    result = script("--dry-run", "--hold-gpu", "--upgrade-gpu", env=NO_PACKAGES)
    assert result.returncode == 2 and result.stdout == ""


def test_bootstrap_installs_the_needrestart_override():
    line = next(line for line in dry_run() if "/etc/needrestart/conf.d/" in line)
    assert line.startswith("+ install -D -m 0644 ") and line.split()[-2].endswith("/stack/host/needrestart.conf")
    assert line.endswith(" /etc/needrestart/conf.d/local-ai.conf")


# How needrestart reads a /etc/needrestart/conf.d/*.conf file: as Perl, after its own config, which
# already overrides some services (dbus stands in for them). Prints each unit's restart setting:
# 0 is never restarted automatically; "default" is restarted when a library it uses was replaced.
NEEDRESTART_EVAL = r"""
our %nrconf = (override_rc => {qr(^dbus) => 0});
my $fn = shift;
eval do { local(@ARGV, $/) = $fn; <> };
die "Error parsing $fn: $@" if $@;
for my $unit (@ARGV) {
  my ($re) = grep { $unit =~ /$_/ } keys %{$nrconf{override_rc}};
  print "$unit ", (defined $re ? $nrconf{override_rc}{$re} : "default"), "\n";
}
"""


@pytest.mark.skipif(shutil.which("perl") is None, reason="perl not installed")
def test_needrestart_never_restarts_a_local_ai_unit():
    # Restart mode is automatic on this box, and llama-swap's unit stops every loaded model when it
    # restarts. The override must add to needrestart's own list, never replace it.
    units = ["local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service",
             "local-ai-pull.service", "ssh.service", "dbus.service"]
    out = subprocess.run(["perl", "-e", NEEDRESTART_EVAL, str(ROOT / "stack/host/needrestart.conf"), *units],
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == [
        "local-ai-llama-swap.service 0",
        "local-ai-brake.service 0",
        "local-ai-compose.service 0",
        "local-ai-pull.service 0",
        "ssh.service default",
        "dbus.service 0",
    ]
```

- [ ] **Step 2: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_bootstrap.py`
  → FAIL: `--upgrade-gpu` is an unknown option (exit 2), `upgrade_gpu` isn't defined, the dry run has
  no needrestart line, `stack/host/needrestart.conf` doesn't exist, and `make` has no `upgrade-gpu`
  target. Phase 0's bootstrap tests still pass.

- [ ] **Step 3: Implement the override and the upgrade mode**

`stack/host/needrestart.conf`:

```perl
# Installed by stack/host/bootstrap.sh as /etc/needrestart/conf.d/local-ai.conf — edit it here.
# After every apt run, needrestart restarts the services still using a library the run replaced —
# automatically, on this box. Restarting llama-swap stops every loaded model (its unit kills the
# whole control group), so the stack's units are left alone: they restart when Dan restarts them,
# or at the next reboot. `sudo needrestart -r l` lists what is still waiting.
$nrconf{override_rc}->{qr(^local-ai-)} = 0;
```

In `stack/host/bootstrap.sh`, the header's usage lines gain:

```bash
#   Move the GPU set:           make upgrade-gpu   (upgrade day, in tmux; it runs this script with
#                               --upgrade-gpu, and make upgrade-gpu-dry-run previews it)
```

The globals, below `HOLD_ONLY=0`:

```bash
UPGRADE=0
REHELD=1     # upgrade day: 0 from the moment the GPU set is released until it is held again
STOPPED=""   # upgrade day: the stack's units it stopped
MOVING=0     # upgrade day: 1 from the moment apt starts moving the set
MOVE_FROM="" # upgrade day: the set's contents just before that (gpu_set_contents)
```

`parse_args` takes the new mode, names it in the unknown-option message, and refuses both modes at
once:

```bash
parse_args() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) DRY_RUN=1 ;;
      --hold-gpu) HOLD_ONLY=1 ;;
      --upgrade-gpu) UPGRADE=1 ;;
      # A mistyped --dry-run under sudo must not turn into a real run.
      *) echo "bootstrap: unknown option '$arg' (the options are --dry-run, --hold-gpu and --upgrade-gpu)" >&2; exit 2 ;;
    esac
  done
  if (( HOLD_ONLY && UPGRADE )); then
    echo "bootstrap: --hold-gpu and --upgrade-gpu are separate modes; pick one" >&2
    exit 2
  fi
}
```

After `hold_gpu_stack`, the upgrade mode.

- `gpu_set_state` reads the same patterns as the hold, so `held_gpu_set` releases exactly the set.
- `gpu_set_contents` drops the hold letter, the first of dpkg's status letters. The release turns it
  from `h` to `i` and the hold turns it back, without moving anything. So a before-and-after pair of
  contents says whether apt moved anything: a package's state or its version.
- `rehold` runs the hold in a subshell, so a hold that stops (a package dpkg didn't finish, a hold
  that didn't take, no kernel, nothing matching) can't end the script before it says so. The hold
  after apt's move, if a signal cuts it off, is tried once more on the way out. A signal during a
  hold the way out runs, the retry included, says what is left to do.
- The way out suggests starting the stack only when nothing in the set moved and the newest kernel
  still has its NVIDIA module (`newest_kernel`, `module_version`, the same check the gate makes);
  otherwise it sends Dan to the recovery, never to a reboot.

```bash
# Upgrade day, as one command (make upgrade-gpu, in tmux). It releases the GPU set, reads apt's plan
# and refuses one that would leave a kernel without its NVIDIA module or change the driver branch,
# moves the set, holds it again with the hold and nothing else, and checks the newest kernel for its
# NVIDIA module before it asks for the reboot. That assumes GRUB boots the newest kernel, which
# this doesn't check: updates.md's GRUB check, which Dan runs before this and again before the
# reboot, reads grub.cfg's entry 0 and default, and grubenv. Every way out after the release holds
# the set again. website/how-to/updates.md has the same steps by hand, and the recovery.

# The GPU set as dpkg has it now: each package's status letters, name and version, found with the
# hold's own patterns.
gpu_set_state() {
  local pattern
  local -a patterns=()
  while IFS= read -r pattern; do patterns+=("$pattern"); done < <(gpu_hold_patterns)
  { dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\t${Version}\n' "${patterns[@]}" 2>/dev/null || true; } |
    LC_ALL=C sort -u
}

# The set's held members: not everything apt-mark lists, so a package held for another reason stays
# held.
held_gpu_set() {
  gpu_set_state | awk '$1 == "hi" {print $2}' | LC_ALL=C sort -u
}

# The set without its hold letter, the first status letter: apt-mark turns ii into hi and back
# without moving anything. Two of these, before and after, say whether apt moved anything in the
# set, a package's state or its version.
gpu_set_contents() {
  gpu_set_state | cut -c2- | LC_ALL=C sort
}

# The newest kernel, which the next boot starts only while GRUB's entry 0 is that kernel and GRUB
# starts entry 0 (updates.md's GRUB check), and the version of the NVIDIA module built for a
# kernel: nothing when it has none.
newest_kernel() {
  linux-version list | linux-version sort --reverse | head -1
}
module_version() {
  modinfo -k "$1" -F version nvidia 2>/dev/null || true
}

# Reads `apt-get -s dist-upgrade` on stdin and prints why the plan must not run. It would remove the
# modules metapackage (which brings in each new kernel's NVIDIA modules), swap it for another driver
# branch's, or install a kernel, linux-image-<version>, with no linux-modules-nvidia-* ending in
# <version>. A kernel counts as new by that name, a digit after "linux-image-"; that DGX OS names its
# kernels so is not yet checked on the box (Phase 1's Task 12 lists them). The newest-kernel check
# before the reboot catches a kernel this misses.
refused_plan() {
  local plan kernel
  plan="$(awk '$1 == "Inst" || $1 == "Remv" {sub(/:.*/, "", $2); print $1, $2}')"
  awk '$2 ~ /^linux-modules-nvidia-.*-nvidia-hwe-/ {if ($1 == "Remv") gone[$2] = 1; else came[$2] = 1}
       END {
         for (g in gone) {
           other = ""
           for (c in came) if (!(c in gone)) other = c
           if (other != "") print "  the driver branch changes: it removes " g " and installs " other
           else print "  it removes " g ", the metapackage that brings in the NVIDIA modules for each new kernel"
         }
       }' <<<"$plan"
  while IFS= read -r kernel; do
    awk -v tail="-$kernel" '$1 == "Inst" && $2 ~ /^linux-modules-nvidia-/ && substr($2, length($2) - length(tail) + 1) == tail {found = 1} END {exit !found}' <<<"$plan" ||
      echo "  it installs the kernel $kernel with no NVIDIA modules for it"
  done < <(awk '$1 == "Inst" && $2 ~ /^linux-image-[0-9]/ {sub(/^linux-image-/, "", $2); print $2}' <<<"$plan")
}

# Holds the set again. A hold that ran to the end and stopped has said what is wrong, and running it
# again would say the same. A hold cut off by a signal (exit status 128 or more) leaves REHELD at 0,
# so the way out tries once more.
rehold() {
  local code=0
  ( hold_gpu_stack ) || code=$?
  if (( code == 0 )); then REHELD=1; return 0; fi
  if (( code < 128 )); then
    REHELD=1
    echo "bootstrap: the GPU set is not held again yet — do what the hold says above, then run make hold-gpu" >&2
  fi
  return 1
}

# A signal while the way out holds the set again: say what is left to do.
cut_off_while_holding() {
  echo "bootstrap: stopped while holding the GPU set again — run make hold-gpu" >&2
  if (( MOVING )); then
    echo "bootstrap: apt may have moved the set, so don't reboot until step 5's module check passes: 'If it goes wrong' in website/how-to/updates.md" >&2
  fi
  exit 130
}

on_upgrade_exit() {
  local code=$?
  trap - EXIT
  trap cut_off_while_holding HUP INT TERM
  if (( ! REHELD )); then
    echo "bootstrap: upgrade day stopped before the end — holding the GPU set again" >&2
    rehold || code=1
  fi
  if (( code != 0 )); then
    # After apt started, the stack may start again only if the set is as it was and the newest
    # kernel still has its module: apt can install a new kernel, outside the set, and then fail.
    local unsafe=""
    if (( MOVING )); then
      if [[ "$(gpu_set_contents)" != "$MOVE_FROM" ]]; then
        unsafe="the GPU set moved before this stopped"
      elif [[ -z "$(module_version "$(newest_kernel)")" ]]; then
        unsafe="the newest kernel has no NVIDIA module"
      fi
    fi
    if [[ -n "$unsafe" ]]; then
      echo "bootstrap: $unsafe. Don't reboot or start the stack yet: follow 'If it goes wrong' in website/how-to/updates.md" >&2
    elif [[ -n "$STOPPED" ]]; then
      echo "bootstrap: nothing in the GPU set moved, so start what was stopped again: systemctl start$STOPPED" >&2
    fi
  fi
  exit "$code"
}

upgrade_gpu() {
  say "upgrade day: move the GPU set as one — website/how-to/updates.md"
  local pkgs refusal unit kernel version
  local -a stack_units=(local-ai-llama-swap.service local-ai-brake.service)
  pkgs="$(held_gpu_set)"
  REHELD=0
  trap on_upgrade_exit EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  if [[ -z "$pkgs" ]]; then
    say "nothing in the GPU set is held; the end of this run holds it"
  else
    # shellcheck disable=SC2086  # one package per word is intended
    run apt-mark unhold $pkgs
  fi
  run dpkg --configure -a
  run apt-get update
  # apt-get's dist-upgrade is apt's full-upgrade: it may remove packages, which moving the set needs.
  # Read the plan first, and refuse it before anything moves.
  if (( DRY_RUN )); then
    printf '+ apt-get -s dist-upgrade   (a real run stops here if the plan would leave a kernel without its NVIDIA module, or changes the driver branch)\n'
  else
    refusal="$(apt-get -s dist-upgrade | refused_plan)"
    if [[ -n "$refusal" ]]; then
      {
        echo "bootstrap: not moving the GPU set, because of apt's plan:"
        echo "$refusal"
        if grep -q 'driver branch' <<<"$refusal"; then
          echo "nothing was installed or removed; a new driver branch is a move planned and made by hand (website/how-to/updates.md)"
        else
          echo "nothing was installed or removed; try again next upgrade day (website/how-to/updates.md)"
        fi
      } >&2
      exit 1
    fi
  fi
  # The engines use the GPU; the reboot starts them again, and the brake with them.
  for unit in "${stack_units[@]}"; do
    if (( DRY_RUN )); then printf '+ systemctl stop %s   (if running)\n' "$unit"; continue; fi
    if systemctl is-active --quiet "$unit"; then
      systemctl stop "$unit"
      STOPPED="$STOPPED $unit"
    fi
  done
  if (( ! DRY_RUN )); then
    MOVE_FROM="$(gpu_set_contents)"
    MOVING=1
  fi
  run apt-get dist-upgrade
  rehold || exit 1
  if (( DRY_RUN )); then
    printf '+ modinfo -k <the newest kernel> -F version nvidia\n'
    return 0
  fi
  kernel="$(newest_kernel)"
  version="$(module_version "$kernel")"
  if [[ -z "$version" ]]; then
    echo "bootstrap: DON'T REBOOT — $kernel, the newest kernel, has no NVIDIA module. See 'If it goes wrong' in website/how-to/updates.md" >&2
    exit 1
  fi
  say "ready: $kernel, the newest kernel, has NVIDIA driver $version — record both, and CUDA's version, in changelog.md"
  say "now: sudo reboot, then make doctor"
}
```

After `earlyoom_config`, the needrestart step:

```bash
needrestart_config() {
  say "needrestart — leave the stack's local-ai-* units alone after an apt run"
  run install -D -m 0644 "$HERE/needrestart.conf" /etc/needrestart/conf.d/local-ai.conf
}
```

In `main`, the upgrade mode runs alone, like `--hold-gpu`, and a full run calls
`needrestart_config` after `earlyoom_config`:

```bash
main() {
  parse_args "$@"
  preflight
  if (( HOLD_ONLY )); then
    # Upgrade day: re-hold the set and nothing else — no desktop stop, no earlyoom restart, no
    # owner and mode resets.
    hold_gpu_stack
    return 0
  fi
  if (( UPGRADE )); then
    upgrade_gpu
    return 0
  fi
  packages
  hold_gpu_stack
  users_and_groups
  directories
  headless
  earlyoom_config
  needrestart_config
  firewall
  polkit_rule
  say "done — continue with website/how-to/bootstrap.md, 'After bootstrap'"
}
```

In the `Makefile`, `.PHONY` gains `upgrade-gpu upgrade-gpu-dry-run`, and (recipe lines start with a
tab; `$$TMUX` is make's escape for the shell's `$TMUX`):

```make
upgrade-gpu-dry-run: ## Print what upgrade day would do to the GPU set; changes nothing
	bash stack/host/bootstrap.sh --upgrade-gpu --dry-run

upgrade-gpu: ## Upgrade day: move the GPU set as one, in tmux (Dan; asks for sudo once)
	@test -n "$$TMUX" || { echo "make upgrade-gpu: run it inside tmux (tmux new -As upgrade), so a dropped SSH session can't stop apt halfway" >&2; exit 1; }
	sudo bash stack/host/bootstrap.sh --upgrade-gpu
```

The tmux check sits in the Makefile because `sudo` drops `$TMUX` from the environment.

- [ ] **Step 4: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`,
  then `make lint`. `make upgrade-gpu-dry-run` on the Mac prints the steps, and its hold step says
  `a real run stops here`: no DGX kernel is installed here.

- [ ] **Step 5: Write the failing tests for `spark doctor`**

`spark/tests/test_doctor.py`:

```python
import argparse
import json
import subprocess
from pathlib import Path

from spark import doctor
from spark.doctor import SECRETS, UFW_CONF, UNITS, checks, earlyoom_args, report
from spark.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")
KEY = "doctor-test-key"
KERNEL = "7.0.0-1019-nvidia"
URL = "http://127.0.0.1:9100"
REPO_ARGS = earlyoom_args((ROOT / "stack/host/earlyoom.default").read_text())
MODULES_QUERY = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n", f"linux-modules-nvidia-*-{KERNEL}")
HOLD_DRY_RUN = tuple(doctor.HOLD_DRY_RUN)


class FakeProbe:
    """A healthy Spark with the stack running, until a test changes it."""

    def __init__(self):
        self.commands = {
            ("git", "config", "core.hooksPath"): (0, ".githooks\n", ""),
            HOLD_DRY_RUN: (0, "==> hold the GPU stack\n==> GPU set: 151 packages, 151 already held\n"
                              "+ apt-mark hold cuda-toolkit-13-0\n", ""),
            ("uname", "-r"): (0, f"{KERNEL}\n", ""),
            MODULES_QUERY: (0, f"hi \tlinux-modules-nvidia-580-open-{KERNEL}\n", ""),
            ("modinfo", "-F", "version", "nvidia"): (0, "580.178\n", ""),
            ("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"): (0, "580.178\n", ""),
            ("systemctl", "is-active", "earlyoom"): (0, "active\n", ""),
            ("systemctl", "show", "--property=MainPID", "--value", "earlyoom"): (0, "4242\n", ""),
            ("systemctl", "is-active", "ufw"): (0, "active\n", ""),
            ("systemctl", "is-active", *UNITS): (0, "active\nactive\nactive\n", ""),
        }
        self.files = {
            Path("/proc/4242/cmdline"): "\0".join(["/usr/bin/earlyoom", *REPO_ARGS]) + "\0",
            doctor.EARLYOOM_DEFAULT: (ROOT / "stack/host/earlyoom.default").read_text(),
            UFW_CONF: "# /etc/ufw/ufw.conf\nENABLED=yes\nLOGLEVEL=low\n",
        }
        self.owners = {SECRETS: ("root", "spark", 0o750)}
        self.open_folders = set()
        self.pages = {f"{URL}/health": 200, "http://127.0.0.1:3000/": 200, "http://127.0.0.1:8888/": 200}
        self.keys_sent = []

    def run(self, argv):
        return self.commands.get(tuple(argv), (127, "", f"{argv[0]}: not found"))

    def read(self, path):
        return self.files.get(Path(path))

    def owner(self, path):
        return self.owners.get(Path(path))

    def listable(self, path):
        return Path(path) in self.open_folders

    def http(self, url, *, key=None, body=None, timeout=10.0):
        self.keys_sent.append(key)
        if url == f"{URL}/running":
            return (200, '{"running": []}') if key == KEY else (401, "")
        if url == f"{URL}/v1/embeddings":
            if key != KEY:
                return 401, ""
            assert body == {"model": "embed", "input": "doctor"}
            return 200, json.dumps({"data": [{"embedding": [0.25, 0.5]}]})
        return self.pages.get(url, 0), ""


def failures(probe, key=KEY):
    return {c.name: c.detail for c in checks(probe, key, REG) if not c.ok}


def test_a_healthy_spark_passes_every_check():
    results = checks(FakeProbe(), KEY, REG)
    assert len(results) == 11 and [c.name for c in results if not c.ok] == []


def test_hooks_that_are_off_say_how_to_turn_them_on():
    probe = FakeProbe()
    probe.commands[("git", "config", "core.hooksPath")] = (1, "", "")
    assert failures(probe) == {"leak hooks": "off in this clone: run `make hooks`"}


def test_an_unheld_member_of_the_gpu_set_fails():
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (0, "==> GPU set: 151 packages, 150 already held\n", "")
    assert failures(probe) == {"GPU set": "1 of 151 packages aren't held: run `make hold-gpu`"}


def test_a_dpkg_run_that_did_not_finish_fails_with_the_holds_own_hint():
    err = ("bootstrap: these GPU-set packages are not cleanly installed, so they can't be held:\n"
           "  nvidia-driver-580-open (iU)\nfinish dpkg first: sudo dpkg --configure -a — then run this again\n")
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (1, "==> hold the GPU stack\n", err)
    detail = failures(probe)["GPU set"]
    assert "nvidia-driver-580-open (iU)" in detail and "sudo dpkg --configure -a" in detail


def test_a_set_the_hold_would_refuse_fails_with_its_reason():
    probe = FakeProbe()
    probe.commands[HOLD_DRY_RUN] = (0, "==> GPU set: 3 packages, 3 already held\n"
                                       "+ apt-mark hold   (a real run stops here: the GPU set has no kernel)\n", "")
    assert failures(probe) == {"GPU set": "the GPU set has no kernel"}


def test_the_running_kernels_modules_must_be_held():
    probe = FakeProbe()
    probe.commands[MODULES_QUERY] = (0, f"ii \tlinux-modules-nvidia-580-open-{KERNEL}\n", "")
    assert failures(probe) == {"running kernel's modules":
                               f"linux-modules-nvidia-580-open-{KERNEL} isn't held: run `make hold-gpu`"}
    probe.commands[MODULES_QUERY] = (1, "", "dpkg-query: no packages found")
    assert "no NVIDIA modules package for the running kernel" in failures(probe)["running kernel's modules"]


def test_the_module_and_nvidia_smi_must_agree():
    # After upgrade day moved the driver, and before the reboot, the two disagree.
    probe = FakeProbe()
    probe.commands[("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")] = (9, "", "mismatch")
    probe.commands[("modinfo", "-F", "version", "nvidia")] = (0, "580.200\n", "")
    assert failures(probe) == {"GPU driver": "kernel module 580.200, nvidia-smi failed: see website/how-to/updates.md"}


def test_earlyoom_must_run_with_the_repos_arguments():
    probe = FakeProbe()
    old = [arg.replace("sshd.*", "sshd") for arg in REPO_ARGS]  # the args before Phase 0's fix
    probe.files[Path("/proc/4242/cmdline")] = "\0".join(["/usr/bin/earlyoom", *old]) + "\0"
    assert "other arguments than stack/host/earlyoom.default" in failures(probe)["earlyoom"]
    probe.commands[("systemctl", "is-active", "earlyoom")] = (3, "inactive\n", "")
    assert failures(probe)["earlyoom"].startswith("inactive")


def test_the_firewall_must_be_on_and_an_unreadable_conf_says_how_to_check():
    probe = FakeProbe()
    probe.files[UFW_CONF] = "ENABLED=no\n"
    assert failures(probe)["firewall"].startswith("ufw is off")
    del probe.files[UFW_CONF]
    assert "sudo ufw status" in failures(probe)["firewall"]


def test_the_secrets_folder_must_stay_closed_to_you():
    probe = FakeProbe()
    probe.open_folders.add(SECRETS)
    assert "can list" in failures(probe)["secrets folder"]
    probe.owners[SECRETS] = ("root", "spark", 0o755)
    assert failures(probe)["secrets folder"].endswith("root:spark 755, not root:spark 750")


def test_a_unit_that_is_down_is_named():
    probe = FakeProbe()
    probe.commands[("systemctl", "is-active", *UNITS)] = (3, "active\ninactive\nactive\n", "")
    assert failures(probe) == {"stack units": "local-ai-brake.service (inactive)"}


def test_llama_swap_must_refuse_a_call_without_a_key():
    probe = FakeProbe()
    probe.http = lambda url, key=None, body=None, timeout=10.0: (200, "")
    assert "keys aren't enforced" in failures(probe)["llama-swap"]


def test_without_a_key_the_key_checks_fail_and_the_others_still_run():
    assert failures(FakeProbe(), key=None) == {
        "llama-swap": "no key in this shell: SPARK_API_KEY isn't set",
        "a model, end to end": "no key in this shell: SPARK_API_KEY isn't set",
    }


def test_a_refused_load_points_at_make_status():
    probe = FakeProbe()
    answer = probe.http
    probe.http = lambda url, key=None, body=None, timeout=10.0: (
        (502, "") if url.endswith("/v1/embeddings") else answer(url, key=key, body=body, timeout=timeout))
    assert failures(probe) == {"a model, end to end": "embed answered 502: `make status` says why a load was refused"}


def test_the_report_shows_every_check_and_never_the_key():
    probe = FakeProbe()
    text = report(checks(probe, KEY, REG))
    assert KEY in probe.keys_sent and KEY not in text
    assert text.splitlines()[0] == "ok    leak hooks: on in this clone"
    assert text.splitlines()[-1] == "doctor: 11 of 11 checks pass"


def test_doctor_runs_only_from_the_repo_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert doctor.run(argparse.Namespace(key_env="SPARK_API_KEY")) == 2
    assert "repo root" in capsys.readouterr().out


def test_make_doctor_runs_spark_doctor():
    # -s: under -C, GNU make 4 also prints "Entering directory" and "Leaving directory" lines.
    recipe = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "doctor"], capture_output=True, text=True, check=True)
    assert recipe.stdout.strip().endswith("spark doctor")
```

And in `spark/tests/test_bootstrap.py`, after `test_needrestart_never_restarts_a_local_ai_unit`, a
test that pins what doctor reads from the hold's real dry run:

```python
def test_doctor_reads_the_holds_dry_run_as_it_is_printed(tmp_path):
    # `make doctor` judges the GPU set from this dry run, so the two agree on what the set is.
    from spark.doctor import judge_gpu_set

    held_set = {pkg: "hi" if status == "ii" else status for pkg, status in INSTALLED.items()}
    cases = {
        "held": (held_set, True, f"all {len(GPU_SET)} packages held"),
        "one-new": ({**held_set, "libcublas-13-0": "ii"}, False, f"1 of {len(GPU_SET)} packages aren't held"),
        "interrupted": ({**held_set, "nvidia-driver-580-open": "iU"}, False, "sudo dpkg --configure -a"),
        "nothing": ({}, False, "nothing installed matches"),
    }
    for name, (installed, ok, words) in cases.items():
        (tmp_path / name).mkdir()
        result = script("--hold-gpu", "--dry-run", env=gpu_env(tmp_path / name, installed))
        check = judge_gpu_set(result.returncode, result.stdout, result.stderr)
        assert check.ok is ok and words in check.detail, (name, check)
```

- [ ] **Step 6: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_doctor.py spark/tests/test_bootstrap.py`
  → FAIL: `ModuleNotFoundError: spark.doctor`.

- [ ] **Step 7: Implement `spark doctor`**

`spark/src/spark/doctor.py`:

```python
"""`spark doctor` (`make doctor`), v0 — Phase 0's guardrails and the stack, checked in one pass.

Run it on the Spark, from the repo root, after any update, a reboot or upgrade day. It only reads:
it changes nothing, needs no sudo, and never prints a key. `spark doctor` proper, one check per
scenario, arrives with the gate in Phase 2.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from spark import paths
from spark.llamaswap import key_from_env
from spark.registry import Registry, load_registry

UNITS = ("local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service")
SECRETS = Path("/etc/local-ai/secrets")
UFW_CONF = Path("/etc/ufw/ufw.conf")
EARLYOOM_DEFAULT = Path("stack/host/earlyoom.default")
# Each should answer 200 on / (urllib follows redirects). Not yet seen on the box: the first
# `make doctor` there is the check.
WEB = (("Open WebUI", "http://127.0.0.1:3000/"), ("SearXNG", "http://127.0.0.1:8888/"))
HOLD_DRY_RUN = ["bash", "stack/host/bootstrap.sh", "--hold-gpu", "--dry-run"]
HOLD_SUMMARY = re.compile(r"^==> GPU set: (\d+) packages, (\d+) already held$", re.MULTILINE)
HOLD_STOPS = re.compile(r"\(a real run stops here: (.+)\)$", re.MULTILINE)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class Probe:
    """What the checks read from the box. The tests hand in a fake instead."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        try:
            done = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as err:
            return 127, "", str(err)
        return done.returncode, done.stdout, done.stderr

    def read(self, path: Path) -> str | None:
        """A file's text, or None if it can't be read. A relative path is inside the repo."""
        try:
            return (self.repo / path).read_text()
        except OSError:
            return None

    def owner(self, path: Path) -> tuple[str, str, int] | None:
        try:
            st = Path(path).stat()
            return pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name, st.st_mode & 0o7777
        except (OSError, KeyError):
            return None

    def listable(self, path: Path) -> bool:
        try:
            os.listdir(path)
        except OSError:
            return False
        return True

    def http(self, url: str, *, key: str | None = None, body: dict | None = None,
             timeout: float = 10.0) -> tuple[int, str]:
        """The HTTP status and body; 0 when nothing answered. The key goes in a header, never a URL."""
        request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode())
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode(errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, ""
        except (urllib.error.URLError, OSError):
            return 0, ""


# Phase 0's guardrails


def hooks(probe: Probe) -> Check:
    _, out, _ = probe.run(["git", "config", "core.hooksPath"])
    if out.strip() == ".githooks":
        return Check("leak hooks", True, "on in this clone")
    return Check("leak hooks", False, "off in this clone: run `make hooks`")


def judge_gpu_set(code: int, out: str, err: str) -> Check:
    """Reads the hold's own dry run (`make hold-gpu-dry-run`), so doctor and the hold agree on what
    the GPU set is."""
    stops = HOLD_STOPS.search(out)
    summary = HOLD_SUMMARY.search(out)
    if code != 0 or stops or not summary:
        why = stops.group(1) if stops else " ".join(err.split()) or "the hold's dry run found no GPU set"
        return Check("GPU set", False, why)
    total, held = int(summary.group(1)), int(summary.group(2))
    if held < total:
        return Check("GPU set", False, f"{total - held} of {total} packages aren't held: run `make hold-gpu`")
    return Check("GPU set", True, f"all {total} packages held")


def gpu_set(probe: Probe) -> Check:
    return judge_gpu_set(*probe.run(HOLD_DRY_RUN))


def running_modules(probe: Probe) -> Check:
    name = "running kernel's modules"
    _, kernel, _ = probe.run(["uname", "-r"])
    kernel = kernel.strip()
    _, out, _ = probe.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n",
                           f"linux-modules-nvidia-*-{kernel}"])
    rows = [line.split() for line in out.splitlines() if len(line.split()) == 2]
    held = [package for status, package in rows if status == "hi"]
    unheld = [package for status, package in rows if status == "ii"]
    if held:
        return Check(name, True, f"{held[0]} is held")
    if unheld:
        return Check(name, False, f"{unheld[0]} isn't held: run `make hold-gpu`")
    return Check(name, False, f"no NVIDIA modules package for the running kernel {kernel}: "
                              "see website/how-to/updates.md")


def driver(probe: Probe) -> Check:
    _, module, _ = probe.run(["modinfo", "-F", "version", "nvidia"])
    _, smi, _ = probe.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    module, smi = module.strip(), (smi.split() or [""])[0]
    if module and module == smi:
        return Check("GPU driver", True, f"{smi}: the kernel module and nvidia-smi agree")
    return Check("GPU driver", False, f"kernel module {module or 'missing'}, nvidia-smi {smi or 'failed'}: "
                                      "see website/how-to/updates.md")


def earlyoom_args(text: str) -> list[str]:
    """EARLYOOM_ARGS from stack/host/earlyoom.default, split as systemd splits it: on spaces."""
    for line in text.splitlines():
        if line.startswith("EARLYOOM_ARGS="):
            return line.removeprefix("EARLYOOM_ARGS=").strip('"').split()
    return []


def earlyoom(probe: Probe) -> Check:
    _, state, _ = probe.run(["systemctl", "is-active", "earlyoom"])
    if state.strip() != "active":
        return Check("earlyoom", False, f"{state.strip() or 'not running'}: `make bootstrap` enables it")
    _, pid, _ = probe.run(["systemctl", "show", "--property=MainPID", "--value", "earlyoom"])
    cmdline = probe.read(Path(f"/proc/{pid.strip()}/cmdline")) or ""
    running = [arg for arg in cmdline.split("\0")[1:] if arg]
    if running != earlyoom_args(probe.read(EARLYOOM_DEFAULT) or ""):
        return Check("earlyoom", False, "running with other arguments than stack/host/earlyoom.default: "
                                        "`make bootstrap` puts the repo's back")
    return Check("earlyoom", True, "active, with the repo's arguments")


def firewall(probe: Probe) -> Check:
    _, state, _ = probe.run(["systemctl", "is-active", "ufw"])
    conf = probe.read(UFW_CONF)
    if conf is None:
        return Check("firewall", False, f"can't read {UFW_CONF}: check by hand with `sudo ufw status`")
    if state.strip() == "active" and any(line.strip() == "ENABLED=yes" for line in conf.splitlines()):
        return Check("firewall", True, "ufw is on")
    return Check("firewall", False, "ufw is off: `sudo ufw status`; bootstrap's firewall step turns it on")


def secrets_folder(probe: Probe) -> Check:
    owner = probe.owner(SECRETS)
    if owner is None:
        return Check("secrets folder", False, f"{SECRETS} is missing: `make bootstrap` creates it")
    user, group, mode = owner
    if (user, group, mode) != ("root", "spark", 0o750):
        return Check("secrets folder", False, f"{SECRETS} is {user}:{group} {mode:o}, not root:spark 750")
    if probe.listable(SECRETS):
        return Check("secrets folder", False, f"your account can list {SECRETS}; it must not")
    return Check("secrets folder", True, "root:spark 750, and closed to you")


# The stack


def units(probe: Probe) -> Check:
    _, out, _ = probe.run(["systemctl", "is-active", *UNITS])
    states = out.split()
    down = [f"{unit} ({state})" for unit, state in zip(UNITS, states) if state != "active"]
    if len(states) != len(UNITS) or down:
        return Check("stack units", False, ", ".join(down) or "systemctl didn't answer")
    return Check("stack units", True, "llama-swap, the brake and the web services are active")


def llama_swap(probe: Probe, key: str | None) -> Check:
    health, _ = probe.http(f"{paths.LLAMASWAP_URL}/health")
    if health != 200:
        return Check("llama-swap", False, f"/health answered {health or 'nothing'}: `make logs s=llama-swap`")
    anonymous, _ = probe.http(f"{paths.LLAMASWAP_URL}/running")
    if anonymous != 401:
        return Check("llama-swap", False, f"/running without a key answered {anonymous or 'nothing'}, "
                                          "not 401: its keys aren't enforced")
    if key is None:
        return Check("llama-swap", False, "no key in this shell: SPARK_API_KEY isn't set")
    keyed, _ = probe.http(f"{paths.LLAMASWAP_URL}/running", key=key)
    if keyed != 200:
        return Check("llama-swap", False, f"/running with your key answered {keyed or 'nothing'}")
    return Check("llama-swap", True, "answers, and refuses a call without a key")


def web(probe: Probe) -> Check:
    down = []
    for name, url in WEB:
        code, _ = probe.http(url)
        if code != 200:
            down.append(f"{name} ({code or 'no answer'})")
    if down:
        return Check("web services", False, ", ".join(down) + ": `make logs s=open-webui` or `s=searxng`")
    return Check("web services", True, "Open WebUI and SearXNG answer")


def model(probe: Probe, key: str | None, registry: Registry | None) -> Check:
    """One request through llama-swap to the embeddings model: loaded if it isn't, on the GPU."""
    name = "a model, end to end"
    if registry is None:
        return Check(name, False, f"no deployed registry at {paths.REGISTRY}: `make apply`")
    models = [m.name for m in registry.models.values() if m.capability == "embeddings"]
    if not models:
        return Check(name, False, "the registry has no embeddings model")
    if key is None:
        return Check(name, False, "no key in this shell: SPARK_API_KEY isn't set")
    code, body = probe.http(f"{paths.LLAMASWAP_URL}/v1/embeddings", key=key,
                            body={"model": models[0], "input": "doctor"}, timeout=300)
    try:
        vector = json.loads(body)["data"][0]["embedding"]
    except (ValueError, KeyError, IndexError, TypeError):
        vector = []
    if code == 200 and vector:
        return Check(name, True, f"{models[0]} answered")
    return Check(name, False, f"{models[0]} answered {code or 'nothing'}: `make status` says why a load "
                              "was refused")


def checks(probe: Probe, key: str | None, registry: Registry | None) -> list[Check]:
    return [
        hooks(probe), gpu_set(probe), running_modules(probe), driver(probe), earlyoom(probe),
        firewall(probe), secrets_folder(probe),
        units(probe), llama_swap(probe, key), web(probe), model(probe, key, registry),
    ]


def report(results: list[Check]) -> str:
    lines = [f"{'ok  ' if c.ok else 'FAIL'}  {c.name}: {c.detail}" for c in results]
    lines.append(f"doctor: {sum(c.ok for c in results)} of {len(results)} checks pass")
    return "\n".join(lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("doctor", help="Phase 0's guardrails and the stack, checked (on the Spark)")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    if not (repo / "stack/host/bootstrap.sh").exists():
        print("doctor: run it from the repo root (make doctor)")
        return 2
    try:
        registry = load_registry(paths.REGISTRY)
    except OSError:
        registry = None
    results = checks(Probe(repo), key_from_env(args.key_env), registry)
    print(report(results))
    return 0 if all(c.ok for c in results) else 1
```

Register in `cli.py`: `from spark import doctor` / `doctor.register(subparsers)`. In the `Makefile`,
`.PHONY` gains `doctor`, and:

```make
doctor: ## On the Spark: Phase 0's guardrails and the stack, checked in one pass
	$(SPARK) doctor
```

- [ ] **Step 8: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`

- [ ] **Step 9: The runbooks say what now exists**
  - `website/how-to/updates.md`, *Any time: apt*: in the needrestart bullet, "From Phase 1 it
    leaves the stack alone too" becomes "Bootstrap installs `/etc/needrestart/conf.d/local-ai.conf`,
    so it leaves the stack's `local-ai-*` units alone too", keeping the rest of the bullet.
  - `updates.md`, *After any update: is everything back?*: the placeholder paragraph becomes the
    steps. Run `make doctor` on the Spark, from the clone. It checks Phase 0's guardrails: the leak
    hooks, the GPU set held (the running kernel's modules package included), the driver's kernel
    module agreeing with `nvidia-smi`, earlyoom running with the repo's arguments, ufw on, and the
    secrets folder closed to you. It checks the stack too: its three units active, llama-swap
    answering and refusing a call without a key, Open WebUI and SearXNG answering, and the
    embeddings model answering through llama-swap, loaded first if it wasn't. Each line is `ok` or
    `FAIL`, and a `FAIL` says what to do. It only reads, needs no sudo, and uses your
    `SPARK_API_KEY`. Then say what brings the stack back by itself: the units are enabled, so a
    reboot starts them; llama-swap and the brake restart if they crash; the containers' restart
    policy brings them back after a Docker upgrade; needrestart leaves the units alone. llama-swap
    preloads nothing, so after a reboot each model loads on its first request.
  - `updates.md`, *Upgrade day: the GPU set*. The edits:
    - **Before the tmux block, a new paragraph.** `make upgrade-gpu` runs steps 1 to 5 as one
      command, in tmux, from the clone; it refuses to start outside tmux. It runs all of them but
      step 5's GRUB check, since it doesn't read GRUB: run that check first, as step 2 says, and
      start `make upgrade-gpu` only once it passes. Run it again once `make upgrade-gpu` says to
      reboot, and reboot only if it passes then too. It releases the set, reads apt's plan
      and refuses it before anything moves if it breaks step 3's rule, stops llama-swap and the
      brake, and moves the set (you read apt's plan and answer). Then it holds the set again with
      `make hold-gpu`'s hold, runs step 5's module check, and says whether to reboot. Every way out
      after the release runs the hold, and only the hold after apt's move is tried again. The set
      can still stay released: when the hold stops (a package dpkg didn't finish, a hold that didn't
      take, no kernel or nothing matching), or when a signal cuts off a hold the way out runs, the
      retry included. In each case it says to run `make hold-gpu`. It judges whether apt moved
      anything by each package's state and version, not the hold letter, and it checks that the
      newest kernel still has its module. If apt moved even part of the set, or left the newest
      kernel without a module, it sends you to *If it goes wrong* rather than to a reboot or a
      restart of the stack. Only when neither happened does it say how to start the stack again.
      `make upgrade-gpu-dry-run` prints the steps without running them. The numbered steps are what
      it runs, to read along with, and to do by hand if it can't. The second GRUB check stays in
      this paragraph until this task's open item on the GRUB check is decided.
    - **Step 1** becomes "Stop what uses the GPU: `systemctl stop local-ai-llama-swap local-ai-brake`
      (the reboot starts them again), and your own GPU jobs and `agent`'s."
    - **Step 2's opening**, which runs step 5's GRUB check before anything moves, went in on
      2026-09-25, before the first upgrade day. Leave it as it is: it covers `make upgrade-gpu` too.
    - **Step 3's answer-no rule** already has its three cases. They went in on 2026-09-25, before
      the first upgrade day, and the first names a removed metapackage with no other in its place.
      Add one sentence after the paragraph that follows them: "`make upgrade-gpu` refuses all three
      too."
    - **Step 5's GRUB check** already reads what GRUB will boot: entry 0's first `linux` line in
      `grub.cfg` against the newest kernel, the `set default=` lines, and `grub-editenv list` for
      a `saved_entry` or `next_entry` with a value. Anything else, or any command failing, is
      "don't reboot yet", and it says how to read the menu to find the kernel GRUB would start.
      It went in on 2026-09-25, before the first upgrade day, so leave it as it is.
    - **Step 7's last sentence** becomes "Then `make doctor`: every line `ok`."
    - The *Not yet performed on this box* markers stay: they cover `make upgrade-gpu` too.
  - `updates.md`, *If it goes wrong*: the first paragraph adds that `make upgrade-gpu` runs the hold
    again by itself on its way out, so `make hold-gpu` by hand is for the manual steps, or for when
    its own hold stopped or was cut off. The second paragraph's bold opening adds "or
    `make upgrade-gpu` said `DON'T REBOOT`, or that apt may have moved the set". Both ways back to a
    reboot already repeat step 5, the GRUB check included (2026-09-25): leave that as it is.
  - `website/how-to/deploy.md`: *Every later change* ends with `make doctor`, and *When something is
    wrong* starts with it: Phase 0's guardrails and the stack in one pass, and each `FAIL` says what
    to do.
  - `README.md` §Contents, brought up to date for everything Phase 1 adds before the switch to the
    Spark:
    - the `Makefile` row: the deploy targets (Task 9), `make upgrade-gpu` and `make doctor`;
    - the `spark/` row: Phase 1's commands (`launch`, `brake`, `status`, `render`, `apply`,
      `models pull`, `clients`, `doctor`);
    - the `stack/` row: `models.yaml`, `templates/`, and `host/needrestart.conf` beside bootstrap,
      earlyoom's config and the polkit rule.

- [ ] **Step 10: Check and commit**

Run: `make test lint docs`
Expected: all pass; `make docs` has no warnings.

```bash
git add stack/host spark Makefile website/how-to/updates.md website/how-to/deploy.md README.md
git commit -m "feat(stack): 🤖 add make upgrade-gpu, make doctor and the needrestart override" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

## ⇄ Switch point — Mac → Spark

- [ ] `make test lint docs` is clean on the Mac, and every `<paste>` in `stack/models.yaml` is a real
  40-hex revision.
- [ ] **Dan OKs the push:** `git push -u origin phase-1`. `gh run watch` — CI is green.
- [ ] **Dan starts the Spark session** with [The Spark session](../how-to/spark-session.md), before
  any **[Spark]** task. That runbook's *Before the first session* steps 2 and 3 (the global rules
  file and the secrets guard) arrived after the Spark's Phase 0 sessions, and nothing records them
  on the box. If the Spark's `~/.claude` lacks either, do them now. Then start the session and check
  the guard first: `/hooks` lists the hook, `/permissions` the deny rules, and the session must be
  refused `test -e ~/.secrets && echo present || echo absent`. Don't go on until it is: Task 13
  Step 1 puts a key in every shell of yours, the session's included.
- [ ] **Two of Phase 0's box steps come before anything here faces the network**, as Phase 0's
  security review asked: keys-only SSH ([SSH from the Mac](../how-to/ssh.md#keys-only), with its
  public IPv6 check) and the Spark's GitHub token for this repository only
  ([The Spark session](../how-to/spark-session.md#github-a-token-for-this-repository-only)). Unless
  `changelog.md` records them, check both, and do whichever is missing now:
  - Keys only: from the Mac,
    `ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive brightroar true`
    ends in `Permission denied (publickey).`, as in ssh.md's own check.
  - The token: on the Spark, `gh auth status` names your account with the token masked. On
    github.com, the Spark's token is the fine-grained one for `chendaniely/local-ai` only, and the
    old *GitHub CLI* authorization is revoked.

  The Spark session records each one newly done in `changelog.md` and README §Current state, in its
  next commit. Task 14 serves the web UI on the tailnet only after both.

***

### Task 11 [Spark]: the engines, at their pins

**Files:**

- Modify: `stack/versions.yaml` (the llama.cpp and whisper.cpp pins), `website/reference/stack.md`
  (regenerated), `changelog.md`, `README.md` §Current state

- [ ] **Step 1: The branch, this session's groups, the driver**

```bash
cd ~/git/hub/local-ai && git fetch && git switch phase-1 && git pull
id -nG                                                        # includes spark-admin and adm
nvidia-smi --query-gpu=driver_version --format=csv,noheader   # 580 or later, for the CUDA 13 builds
/usr/local/cuda/bin/nvcc --version | tail -1                  # CUDA 13.x, which builds whisper.cpp
```

Expected: as commented. (`nvidia-smi` is fine for the driver version; it's the memory figures it
can't report on GB10.) If `id -nG` lacks `spark-admin`, this session started before bootstrap: Dan
logs out, runs `tmux kill-server`, logs back in and restarts the session — the tmux server keeps the
groups it started with. If the driver is older than 580, stop and tell Dan: the prebuilt llama.cpp
needs it, and driver upgrades are held on purpose.

- [ ] **Step 2: llama-swap v257** — its checksum is already in `stack/versions.yaml`:

```bash
tmp=$(mktemp -d)
curl -fsSL -o "$tmp/llama-swap.tar.gz" \
  https://github.com/mostlygeek/llama-swap/releases/download/v257/llama-swap_257_linux_arm64.tar.gz
echo "8fb15ff81108064eaedf95af58212be9d7f43775841e1099387dfa823e5ca2b1  $tmp/llama-swap.tar.gz" | sha256sum -c -
tar -xzf "$tmp/llama-swap.tar.gz" -C "$tmp"
install -D -m 0755 "$tmp/llama-swap" /opt/local-ai/bin/llama-swap/v257/llama-swap
/opt/local-ai/bin/llama-swap/v257/llama-swap -version
```

Expected: `OK` from `sha256sum`; a version line naming 257.

- [ ] **Step 3: llama.cpp b11146 — the prebuilt arm64 + CUDA 13.4 build**

```bash
b=b11146; d=/opt/local-ai/bin/llama.cpp/$b; tmp=$(mktemp -d)
rel=https://github.com/ggml-org/llama.cpp/releases/download/$b
for f in llama-$b-bin-ubuntu-cuda-13.4-arm64.tar.gz cudart-llama-$b-bin-ubuntu-cuda-13.4-arm64.tar.gz; do
  curl -fsSL -o "$tmp/$f" "$rel/$f"
done
# Check both files against the digests GitHub publishes for the release's assets.
curl -fsSL "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$b" \
  | jq -r '.assets[] | select(.name | test("ubuntu-cuda-13.4-arm64")) | "\(.digest | ltrimstr("sha256:"))  \(.name)"' \
  | (cd "$tmp" && sha256sum --ignore-missing -c -)
mkdir -p "$tmp/x" && for f in "$tmp"/*.tar.gz; do tar -xzf "$f" -C "$tmp/x"; done
srv=$(find "$tmp/x" -type f -name llama-server | head -1)
mkdir -p "$d" && cp -a "$(dirname "$srv")"/. "$d"/
find "$tmp/x" -name 'lib*.so*' -exec cp -a {} "$d"/ \;   # the CUDA runtime goes beside the binaries
ldd "$d/llama-server" | grep 'not found' || echo "all libraries found"
"$d/llama-server" --version
"$d/llama-server" --list-devices
/usr/local/cuda/bin/cuobjdump --list-elf "$d"/libggml-cuda.so* 2>/dev/null | grep -o 'sm_[0-9]*[a-z]*' | sort -u
```

Expected: both files `OK`; all libraries found; a version line naming b11146; the GB10 as a CUDA
device; `sm_121a` in the list — native code for this GPU, so a first load doesn't compile PTX. (No
`libggml-cuda.so`? Run `cuobjdump` on `llama-server` itself.) If there's no `sm_121`, note it in
the changelog: Phase 5's bake-off compares a source build. **[Dan]** checks that `agent` reaches the
GPU without docker: `sudo -u agent /opt/local-ai/bin/llama.cpp/b11146/llama-server --list-devices`
lists the same device.

- [ ] **Step 4: whisper.cpp v1.9.4, built for this GPU** (there's no CUDA prebuilt)

```bash
v=v1.9.4; src=~/src/whisper.cpp; d=/opt/local-ai/bin/whisper.cpp/$v
git clone --depth 1 --branch $v https://github.com/ggml-org/whisper.cpp "$src"
git -C "$src" rev-parse HEAD                    # the commit that step 5 pins
cmake -S "$src" -B "$src/build" -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=121a-real \
  -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc
cmake --build "$src/build" -j --config Release --target whisper-server
install -D -m 0755 "$src/build/bin/whisper-server" "$d/whisper-server"
ldd "$d/whisper-server" | grep 'not found' || echo "all libraries found"
"$d/whisper-server" --help | head -3
```

Expected: the build finishes with no warning about an unsupported architecture; all libraries
found. Keep the clone: `samples/jfk.wav` is Task 13's speech test.

- [ ] **Step 5: Pins, changelog, README; commit**

In `stack/versions.yaml`: `llama.cpp` → `pin: sha256:<digest of llama-b11146-bin-ubuntu-cuda-13.4-arm64.tar.gz>`;
`whisper.cpp` → `pin: git:<the commit from step 4>`. Run
`uv run --frozen --project spark spark docs stack --write`. Add a dated `changelog.md` entry (the three
engines and where they live, checksums verified, the driver version, the `sm_` list) and a line to
`README.md` §Current state.

```bash
git add stack/versions.yaml website/reference/stack.md changelog.md README.md
git commit -m "build(stack): 🤖 install and pin the engines on brightroar" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 12 [Spark + Dan]: deploy the config, pull the models

- [ ] **Step 1 [Dan, then Spark]: the host, at this branch** — before anything runs as `spark`.
  Phase 0's review changed bootstrap after it last ran on the box: `/var/lib/local-ai` is now root's
  (a `spark`-owned parent let a re-run hand `spark` a directory of its choosing), and earlyoom avoids
  `sshd.*`. This phase adds `spark`'s two cache folders and the needrestart override. From the clone
  on `phase-1`, over SSH with nothing open on the desktop (bootstrap stops a running desktop and
  restarts earlyoom), Dan runs `make bootstrap-dry-run`, reads it, then runs `make bootstrap`. Then
  the Spark session checks:

```bash
stat -c '%U:%G %a %n' /var/lib/local-ai /var/lib/local-ai/cache /var/lib/local-ai/cuda-cache
cmp stack/host/needrestart.conf /etc/needrestart/conf.d/local-ai.conf && echo "needrestart: same"
cmp stack/host/earlyoom.default /etc/default/earlyoom && echo "earlyoom: same"
diff <(make -s hold-gpu-dry-run | sed -n 's/^+ apt-mark hold //p' | tr ' ' '\n') \
     <(make -s upgrade-gpu-dry-run | sed -n 's/^+ apt-mark unhold //p' | tr ' ' '\n') && echo "release = hold"
dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 'linux-image-[0-9]*'   # the kernels' package names
uname -r
```

Expected: `root:root 755 /var/lib/local-ai`, then `spark:spark 750` for each cache folder; both
files the same as the repo's; and `release = hold`: upgrade day releases exactly the packages the
hold holds, nothing else. `/home/agent/work` from the first bootstrap stays as it is, `agent`'s own;
bootstrap no longer touches it.

Then Dan runs the GRUB check from `website/how-to/updates.md` step 5, whose `grep`s of `grub.cfg`
run with sudo, and tells the Spark session what it printed, leaving out the `root=UUID=…`. With
the last two lines above, it checks two things `make upgrade-gpu` assumes (Task 10) and nothing
has checked yet:

- GRUB boots the newest kernel, the one its pre-reboot check reads: the GRUB check passes. Entry
  0's first `linux` line in `grub.cfg` is the newest kernel, GRUB starts entry 0, and no
  `next_entry` has a value. If it doesn't pass, the newest kernel may not be what GRUB boots.
  `/etc/default/grub` and `/etc/default/grub.d/*.cfg` alone can't show this: `GRUB_FLAVOUR_ORDER`,
  `GRUB_TOP_LEVEL` and indented or exported settings all change what GRUB boots, and `grub.cfg`
  shows the result.
- The installed kernels are named `linux-image-<version>`, with a digit first and the same
  `<version>` as `uname -r` prints for the running one. That is the name its plan check reads.

If either differs, stop and tell the Mac session: that check must change before an upgrade day
relies on it. Task 13 Step 7 records the re-run and both results in the changelog.

- [ ] **Step 2 [Spark]: render and deploy** — `make apply-dry-run`, then `make apply`.
  Expected: every file under `/opt/local-ai/etc` and the app are listed as new; `llama-swap -validate`
  prints `config is valid: 4 model(s)`; `uv sync` builds `/opt/local-ai/app/.venv`; each unit is
  reported as not installed yet.

- [ ] **Step 3 [Dan]: install the units** — `make install-units` (asks for sudo once). Check:
  `systemctl list-unit-files 'local-ai-*'` shows llama-swap, brake and compose `enabled`, and pull
  `linked`.

- [ ] **Step 4 [Spark]: pull the model files** — `make pull`. About 40 GB, downloaded by the `spark`
  user; it prints nothing until it finishes, so follow it with `journalctl -fu local-ai-pull` in
  another tmux pane.
  Expected: five `pull: … → /var/lib/local-ai/hf/hub/models--…/snapshots/<revision>/<file>` lines
  and exit 0. Each path is the one the rendered config hands its engine — compare with
  `grep -o '/var/lib/local-ai/hf/hub/[^ ]*' /opt/local-ai/etc/llama-swap.yaml`. A `FAILED` line means
  a wrong file name or revision: fix `stack/models.yaml`, commit, `make apply`, `make pull` again.
  Then `df -h /`, and note the space left for the changelog.

***

### Task 13 [Spark + Dan]: start the stack, smoke-test it, first footprint readings

- [ ] **Step 1 [Dan]: your key on the Spark** — the checks below use `SPARK_API_KEY`, with the same
  value as on the Mac (one key per person, not per machine). From here on it is in every shell of
  yours, the Spark session's included, so the session's secrets guard must already hold (the Mac →
  Spark switch point). From the Mac, without displaying it:
  `( . ~/.secrets; printf 'export SPARK_API_KEY=%s\n' "$SPARK_API_KEY" ) | ssh brightroar 'umask 077; cat >> ~/.secrets'`.
  On the Spark, once, load it in every shell — as the first line of `~/.bashrc`, above Ubuntu's early
  return for non-interactive shells:
  `grep -q '\.secrets' ~/.bashrc || sed -i '1i [ -f ~/.secrets ] && . ~/.secrets' ~/.bashrc`.
  Restart the Spark session. Check: `python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"`
  → `True`.

- [ ] **Step 2: Start it** (the key goes to curl on stdin, never on its command line — see Global
  Constraints)

```bash
api() { curl -fsS -H @- "$@" <<<"Authorization: Bearer $SPARK_API_KEY"; }
systemctl start local-ai-llama-swap local-ai-brake local-ai-compose
systemctl is-active local-ai-llama-swap local-ai-brake local-ai-compose   # active, three times
curl -fsS http://127.0.0.1:9100/health; echo                               # answers without a key
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9100/running     # 401: keys are enforced
api http://127.0.0.1:9100/running | jq -c .running                         # []
ss -ltn | grep -E ':(9100|3000|8888) '                                     # 127.0.0.1 only
make status
```

Expected: as commented. Nothing is loaded yet — Phase 1 has no preload, so each model loads on its
first request and stays (ttl 0). Open WebUI takes a minute on its first start (`make logs s=open-webui`).

- [ ] **Step 3 [Dan]: Open WebUI's first account, now** — before anything else. From the Mac, in a
  spare terminal, `ssh -N -L 3000:127.0.0.1:3000 brightroar`; open `http://127.0.0.1:3000` and
  create your account. Phase 0's research found that the first account can sign up even with
  `ENABLE_SIGNUP` false, and becomes the admin; after it, signup is closed. That is not yet tried on
  this box. Ctrl-C closes the tunnel.

  Why now: from `local-ai-compose`'s start until that account exists, whoever reaches the page
  first becomes the admin. That is every local user on the Spark, `agent` included, on
  127.0.0.1:3000, and from Task 14 every device on the tailnet. An admin reads every chat and can add
  Functions, Python that runs inside the container as root, with host networking. Task 14 serves the
  page on the tailnet only after this step, and there you log in; you don't sign up.

  **If the page refuses even the first signup**, have Open WebUI make the admin from
  `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD` in `open-webui.env`, then take them out again. This
  route isn't tried here either. On the Spark, in `secret-files.md`'s pattern: you type both at
  prompts, the password isn't shown, and no value goes into the repo:

```bash
sudo bash -c 'IFS= read -rp "admin email: " e; IFS= read -rsp "admin password: " p; echo; q=$(printf "\047"); case "$e$p" in *"$q"*|*\\*) echo "no single quote or backslash in either, please: the env file quotes each value with single quotes" >&2; exit 1 ;; esac; umask 027; printf "WEBUI_ADMIN_EMAIL=%s%s%s\nWEBUI_ADMIN_PASSWORD=%s%s%s\n" "$q" "$e" "$q" "$q" "$p" "$q" >> /etc/local-ai/secrets/open-webui.env; chgrp spark /etc/local-ai/secrets/open-webui.env'
systemctl restart local-ai-compose
```

  Both values go in as typed, a space at either end included (`IFS=` keeps it; plain `read` would
  drop it), and single-quoted, so Compose reads them literally. Unquoted, it expands a `$` and
  cuts a value at ` #`: Compose v5.5.1 reads `ab$cd #x` as `ab`. A single quote can't go inside that
  quoting, and nor can a backslash at the end: Compose reads `\'` as an escaped quote, so the quote
  stays open, and Compose's error prints the value into the journal. So the command refuses a value
  with a single quote or any backslash, writes nothing and prints neither value; choose a password
  without them.

  Log in through the tunnel with that email and password. Then remove both lines, and restart once
  more so the container no longer holds them:

```bash
sudo bash -c 'umask 027 && f=/etc/local-ai/secrets/open-webui.env && grep -v -e "^WEBUI_ADMIN_EMAIL=" -e "^WEBUI_ADMIN_PASSWORD=" "$f" > "$f.new" && chgrp spark "$f.new" && mv "$f.new" "$f"'
systemctl restart local-ai-compose
```

- [ ] **Step 4: One request per model, with memory readings** — from nothing loaded, one at a time.
  `reading` prints how long the request took and MemAvailable before it, the lowest while it ran
  (sampled ten times a second) and after it:

```bash
api() { curl -fsS -H @- "$@" <<<"Authorization: Bearer $SPARK_API_KEY"; }
avail() { awk '/MemAvailable/ {printf "%.1f\n", $2/1048576}' /proc/meminfo; }
reading() {  # reading <label> <output file> <command…>
  local before log sampler t0=$SECONDS took; before=$(avail); log=$(mktemp)
  ( while :; do avail; sleep 0.1; done ) > "$log" & sampler=$!
  "${@:3}" > "$2"; took=$((SECONDS - t0)); sleep 3; kill "$sampler"
  printf '%s: %ss; before %s, lowest %s, after %s GiB\n' "$1" "$took" "$before" "$(sort -n "$log" | head -1)" "$(avail)"
}
chat() { api -H 'Content-Type: application/json' http://127.0.0.1:9100/v1/chat/completions \
  -d "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in five words.\"}],\"max_tokens\":512}"; }
embed() { api -H 'Content-Type: application/json' http://127.0.0.1:9100/v1/embeddings \
  -d '{"model":"qwen3-embedding-0.6b","input":"hello"}'; }
stt() { api http://127.0.0.1:9100/v1/audio/transcriptions -F model=whisper-large-v3-turbo \
  -F file=@"$HOME/src/whisper.cpp/samples/jfk.wav" -F response_format=verbose_json; }

out=$(mktemp -d)
reading gemma-4-26b-a4b "$out/chat.json" chat gemma-4-26b-a4b
reading qwen3-embedding-0.6b "$out/embed.json" embed
reading whisper-large-v3-turbo "$out/stt.json" stt
reading qwen3.6-35b-a3b "$out/coder.json" chat qwen3.6-35b-a3b
jq -r '.model, .choices[0].message.content' "$out/chat.json" "$out/coder.json"
jq '.data[0].embedding | length' "$out/embed.json"
jq -r '.text, (.segments[0].words | length)' "$out/stt.json"
```

Expected: four readings; each chat reply names the model that was asked for (the coder may spend its
tokens thinking — then `.choices[0].message.reasoning_content` holds them); a 1024-dimension
embedding; the JFK sample's text, and a word count above zero (`verbose_json` carries word timings).

- [ ] **Step 5: What runs, as whom, and what the OOM killers would pick**

```bash
make status
ps -o pid=,user=,oomadj=,oom=,rss=,comm= -C llama-server,whisper-server   # oomadj: oom_score_adj; oom: oom_score; rss: KiB
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
journalctl -u local-ai-llama-swap -u local-ai-pull -b --no-pager | grep -ci 'permission denied'
make doctor
```

`oomadj` and `oom` are procps-ng's names for `oom_score_adj` and `oom_score`. Ubuntu 24.04's `ps`
(procps-ng 4.0.4) rejects `oom_score_adj=` as a format.

Expected: four models loaded (three resident, the coder on demand), the brake off, and the headroom
before the brake; every engine runs as `spark` with `1000` in the `oomadj` column. `spark launch`
set that, so the engines are the first processes the kernel or earlyoom would kill. The journal count is `0`:
as far as the logs show, no engine or download was refused a write in `spark`'s home, which is
root's now. If it isn't, the lines name the path: record it for the Mac session, which points that
tool's cache at `/var/lib/local-ai/cache` in the unit template (Task 6's). `make doctor` ends
`doctor: 11 of 11 checks pass`. If its firewall line says it can't read `/etc/ufw/ufw.conf`, record
the file's mode (`stat -c '%a %U:%G' /etc/ufw/ufw.conf`) for the Mac session, which then changes
that check. Step 3's browser already loaded Open WebUI's page. What is not yet seen on this box is
doctor's own expectation: `200` from `/` on both Open WebUI and SearXNG. This first `make doctor`
is that check. If its web line fails, look at the pages in a browser. Step 3's tunnel forwards only
Open WebUI's port 3000; to see SearXNG, open a second one,
`ssh -N -L 8888:127.0.0.1:8888 brightroar`, and load `http://127.0.0.1:8888`. If they work there,
record what doctor says each answered for the Mac session.

Record each engine's `rss` and `oom` columns beside `nvidia-smi`'s per-process memory (GB10 may
print `[N/A]` there; record what it prints). Whether a model's memory counts toward its engine's
RSS on GB10 is not yet known. If it doesn't (an engine's `rss` far below its model's size),
earlyoom's choice among engines is arbitrary. Task 17's forward look then decides whether
`spark launch` should give resident models a lower `oom_score_adj` than on-demand ones, for example
900 against 1000, so earlyoom agrees with the brake's order.

Then **[Dan]** checks earlyoom's victim with the engines loaded, without killing anything: Phase 0's
Task 11 Step 2 check, with the repo's current regexes, for about five seconds, then Ctrl-C:

```bash
sudo earlyoom --dryrun -r 1 -M 125829120,125829110 -s 100,100 \
  --prefer '^(llama-server|whisper-server|VLLM::EngineCor)$' \
  --avoid '^(sshd.*|systemd|systemd-.*|tmux.*|tailscaled|dockerd|containerd|llama-swap|spark)$'
```

Expected: the process it would kill is an engine; note which one.

- [ ] **Step 6: Footprints** — a model's footprint is about *before − lowest*. Where a reading is
  above the registry's estimate (19, 1.5, 3 and 29 GiB), raise that model's `footprint_gib` to the
  reading, rounded up, and leave `footprint_measured: false` — Phase 2 measures at full context
  after a soak. Then `make apply`: only `models.yaml` changed, so only the brake restarts.

- [ ] **Step 7: Changelog, README; commit** — `changelog.md`: Task 12 Step 1's bootstrap re-run
  (`/var/lib/local-ai` root's, the two cache folders, earlyoom avoiding `sshd.*`, the needrestart
  override), with the GRUB check's result and the kernels' package names; units installed and
  running, the model files pulled and the disk space left, the four readings and load times, the
  engines' `oomadj`, `oom` and `rss` beside `nvidia-smi`'s figures, and earlyoom's dry-run victim.
  For GRUB, record whether it passed and the default it uses: `0`, `saved` or a number, never an id
  or a `root=` line, which carry the root filesystem's UUID.
  `README.md` §Current state: the new host layout, what runs, on which ports (127.0.0.1 only), which
  models.

```bash
git add stack/models.yaml changelog.md README.md
git commit -m "docs(machine): 🤖 record the first deploy and footprint readings on brightroar" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 14 [Dan]: the web UI on the phone (S09, S20)

- [ ] **Step 1: Serve it on the tailnet** — the phase's first web exposure, so only once Task 13
  Step 3 made the admin account, and keys-only SSH and the Spark's repo-only GitHub token are done
  (the Mac → Spark switch point): `sudo tailscale serve --bg --https=443 http://127.0.0.1:3000`, then
  `tailscale serve status`. Read the output privately: the HTTPS address names the tailnet.
- [ ] **Step 2: Log in** — on the phone with Tailscale on, open the address and log in with the
  account from Task 13 Step 3. In a private tab, confirm that a second signup is refused.
  Optional: Admin Panel → Settings → Models, and hide `qwen3-embedding-0.6b` and
  `whisper-large-v3-turbo` from the chat picker (they're listed because llama-swap lists every model).
- [ ] **Step 3: S09, the phone away from home** — on mobile data, Tailscale on: add the page to the
  home screen; ask about a photo; dictate a message with the microphone. Each answer is labelled
  `gemma-4-26b-a4b`.
- [ ] **Step 4: S20, web search from the phone** — turn on web search in a chat and ask about
  something from this week; the answer cites its sources.
- [ ] **Step 5:** give the Spark session the date each one passed; it goes in the changelog now and on
  the scenario pages at the close.

***

### Task 15 [Dan + Spark]: pi on the Mac, and as `agent` in tmux

- [ ] **Step 1 [Dan, on the Mac]: pi through the tunnel**

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.85.1   # down from 0.86.1, inside the crash range
pi --version                                                               # 0.85.1
python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"      # True
cd ~/git/hub/local-ai && make clients                                      # the spark provider, old file backed up
make tunnel                                                                # in a spare terminal; leave it open
```

In pi: `/model` → `qwen3.6-35b-a3b`, and a small real task in a scratch repo. Expected: it finishes,
and the footer names `qwen3.6-35b-a3b`.

- [ ] **Step 2 [Dan]: `agent`'s Claude Code gets the secrets guard, before `agent` holds a key** —
  Step 3 puts a key in `agent`'s `~/.secrets`, and `agent`'s Claude Code (Phase 0) runs in its
  shells. Give it the guard the Spark session got in [The Spark session](../how-to/spark-session.md)'s
  steps 2 and 3, written through `agent`'s own login from the Mac, never as root:
  - `ssh brightroar-agent 'mkdir -p ~/.claude'`, then copy the script the hook runs to the same
    place under `agent`'s `~/.claude` (`scp … brightroar-agent:.claude/…`).
  - `ssh brightroar-agent`, and add the same `PreToolUse` hook and deny rules to `agent`'s
    `~/.claude/settings.json`, with every `/Users/dan` in a path changed to `/home/agent`.
  - A `~/.claude/CLAUDE.md` for `agent` that holds only your global file's secrets rule, not the
    rest of that file: `agent` works on untrusted input, and the rest is yours. Put the section in a
    file on the Mac, then `scp` it to `brightroar-agent:.claude/CLAUDE.md`.
  - Check it as the Spark session's first action does: in `agent`'s `claude`, `/hooks` lists the
    hook, `/permissions` the deny rules, and `test -e ~/.secrets && echo present || echo absent` is
    refused.

- [ ] **Step 3 [Dan, on the Spark]: Node, and the agent's key**

```bash
d=$(mktemp -d) && curl -fsSL https://deb.nodesource.com/setup_22.x -o "$d/nodesource_setup.sh" && less "$d/nodesource_setup.sh"
sudo bash "$d/nodesource_setup.sh" && sudo apt-get install -y nodejs   # only once you've read it: it runs as root
node --version                                                          # v22.19 or later
rm -r "$d"
sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; [ -n "$LLAMASWAP_KEY_AGENT" ] || { echo "no LLAMASWAP_KEY_AGENT" >&2; exit 1; }; printf "export SPARK_API_KEY=%s\n" "$LLAMASWAP_KEY_AGENT" | runuser -u agent -- sh -c "umask 077; cat > /home/agent/.secrets"'
```

The Node script goes into a fresh private folder from `mktemp -d`. At a fixed `/tmp` name, a file
`agent` made first would be the one you read and then run as root, and `agent` could change it in
between. The last line copies the agent's own llama-swap key into its home without displaying it.
Root only reads the service secrets; `runuser -u agent` runs the `sh` that writes the file as
`agent`, so a link `agent` planted at `~/.secrets` can't turn it into a root write (Global
Constraints). `printf` is a builtin, so the value never reaches a command line. If the key is
missing, the line refuses and writes nothing, as `secret-files.md` step 5 does. Run again, it
rewrites the file with the same line.

- [ ] **Step 4 [Dan, as `agent`]: pi and uv for the agent** — `sudo -iu agent`, then:

```bash
grep -q '\.secrets' ~/.bashrc || sed -i '1i [ -f ~/.secrets ] && . ~/.secrets' ~/.bashrc
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g --prefix ~/.local --ignore-scripts @earendil-works/pi-coding-agent@0.85.1
git clone --branch phase-1 https://github.com/chendaniely/local-ai ~/work/local-ai   # makes ~/work too
exit
```

`agent` makes its own `~/work` with that clone; bootstrap doesn't, since it runs as root. Then, in a
fresh login as `agent` (`ssh brightroar-agent` from the Mac), so the key and `~/.local/bin` load:

```bash
python3 -c "import os; print(bool(os.environ.get('SPARK_API_KEY')))"   # True
pi --version                                                             # 0.85.1
cd ~/work/local-ai && uv run --frozen --project spark spark clients pi --write
tmux new -As work
```

In tmux: `pi`, `/model` → `qwen3.6-35b-a3b`, and a small real task in `~/work`. Detach (`Ctrl-b d`), log
out, log back in, and `tmux attach -t work` — the session and its output are still there. In a second
tmux window, `claude` starts already logged in (Phase 0); it talks to Anthropic, not the Spark.
Expected: pi finishes the task; its footer names `qwen3.6-35b-a3b`; reattaching works. The agent's
clone is only for `spark clients` — the agent never commits to this repo.

- [ ] **Step 5 [Spark]: Changelog, README; commit** — record Node 22 from NodeSource, pi 0.85.1 and uv
  for `agent`, the agent's key (by reference) and its Claude Code secrets guard, the Mac's pi pinned
  to 0.85.1, and the S09 and S20 dates from Task 14.

```bash
git add changelog.md README.md
git commit -m "docs(machine): 🤖 record pi for agent and the web UI on the tailnet" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 16 [Spark + Dan]: drills — the brake, a load that doesn't fit, a fresh clone, an upgrade and a reboot

- [ ] **Step 1: The brake at raised thresholds (S05)** — load the coder, then run one brake tick
  against a copy of the registry whose thresholds sit just above what's available now, so the brake
  fires while the box still has plenty of memory:

```bash
coder() { curl -s -o /dev/null -w '%{http_code}\n' -H @- -H 'Content-Type: application/json' \
  http://127.0.0.1:9100/v1/chat/completions \
  -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  <<<"Authorization: Bearer $SPARK_API_KEY"; }
coder                                             # 200: the coder is loaded
drill=$(mktemp -d); cp /opt/local-ai/etc/models.yaml "$drill/"
a=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
sed -i "s/warn_gib: 28/warn_gib: $((a + 10))/; s/brake_gib: 20/brake_gib: $((a + 5))/; s/reserve_gib: 24/reserve_gib: $((a + 6))/" "$drill/models.yaml"
SPARK_REGISTRY="$drill/models.yaml" uv run --frozen --project spark spark brake --once
make status
```

Expected: `brake: holding new loads until `spark brake --release`` and
`brake: unloaded qwen3.6-35b-a3b at … GiB available` — the on-demand model goes first and the
residents stay; `make status` shows HOLDING with the coder unloaded.

- [ ] **Step 2: While the hold stands, the coder can't come back**

```bash
coder() { curl -s -o /dev/null -w '%{http_code}\n' -H @- -H 'Content-Type: application/json' \
  http://127.0.0.1:9100/v1/chat/completions \
  -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  <<<"Authorization: Bearer $SPARK_API_KEY"; }
coder                                                      # not 200: the start is refused
make status                                                # refused  qwen3.6-35b-a3b at …: the memory brake has held new loads since …
uv run --frozen --project spark spark brake --release      # brake: hold released
coder                                                      # 200 again
```

Expected: as commented. The refused start leaves nothing running, so there's no reload thrash.

- [ ] **Step 3: A load that doesn't fit is refused, and nothing is unloaded (S03, previewed)** —
  unload the coder, then hold memory with a throwaway process until about 45 GiB is left: above the
  brake's warn line (28), below what the coder needs (29 plus the 24 GiB reserve). This is the one
  drill that really fills memory, so it also records swap, and what the OOM killers would pick:

```bash
coder() { curl -s -o /dev/null -w '%{http_code}\n' -H @- -H 'Content-Type: application/json' \
  http://127.0.0.1:9100/v1/chat/completions \
  -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  <<<"Authorization: Bearer $SPARK_API_KEY"; }
curl -fsS -X POST -H @- http://127.0.0.1:9100/api/models/unload/qwen3.6-35b-a3b <<<"Authorization: Bearer $SPARK_API_KEY"; echo
log=$(mktemp); vmstat -n 1 > "$log" & vm=$!   # swap in (si) and out (so), once a second
a=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
python3 -c "import time; x = b'\x01' * ($((a - 45)) << 30); time.sleep(900)" > /dev/null 2>&1 & hog=$!
sleep 30; make status          # ~45 GiB available; three residents loaded; brake off
coder                          # not 200
make status                    # refused  qwen3.6-35b-a3b at …: needs ~29 GiB, 45 GiB available (24 GiB reserve kept)
for p in $(ps -o pid= -C llama-server,whisper-server) "$hog"; do echo "$(cat "/proc/$p/comm") oom_score $(cat "/proc/$p/oom_score")"; done
kill "$hog"; sleep 5; coder    # 200 once the memory is back
kill "$vm"; awk 'NR > 3 {si += $7; so += $8; if ($3 > most) most = $3} END {print "swap used at most", most + 0, "KiB; swapped in", si + 0, "KiB, out", so + 0, "KiB"}' "$log"
```

Expected: as commented, with the three residents loaded throughout. Every engine's `oom_score` is
above the hog's (`python3`), so the kernel and earlyoom would pick an engine before the hog; record
the figures. The last line says whether memory went to swap. The box has a 16 GiB swap file, and
earlyoom ignores swap (`-s 100,100`). Whether anonymous memory swaps out before `MemAvailable` reaches
the brake is not yet known. Record the line; Task 17's forward look sets swap size and swappiness
from it (plan.md, *To verify on the box*).

- [ ] **Step 4: A fresh clone reproduces the deploy** — first, **Dan OKs pushing** the Spark's
  commits (`git push`), so the clone has everything. The bootstrap here is a real re-run: it stops a
  running desktop and restarts earlyoom, so Dan runs it over SSH with nothing open on the desktop, or
  from the console. Then:

```bash
fresh=$(mktemp -d) && git clone --branch phase-1 https://github.com/chendaniely/local-ai "$fresh/local-ai"
cd "$fresh/local-ai"
make bootstrap           # [Dan], in this directory: sudo; every step is already in place
make apply               # apply: nothing to change
cd ~ && rm -rf "$fresh"
```

Expected: bootstrap finishes without error, and apply prints `apply: nothing to change` — the
running stack is exactly what the repo describes.

- [ ] **Step 5 [Dan + Spark]: a routine `apt upgrade`, then a reboot (S23)** — after each, the stack
  must serve again with no hand on it. With the residents loaded, the Spark session notes what runs
  and since when:

```bash
api() { curl -fsS -H @- "$@" <<<"Authorization: Bearer $SPARK_API_KEY"; }
make doctor                                                                # doctor: 11 of 11 checks pass
api http://127.0.0.1:9100/running | jq -r '.running[].model'               # the models loaded now
systemctl show -p ActiveEnterTimestamp local-ai-llama-swap local-ai-brake  # when each unit started
```

**[Dan]** runs `sudo apt update && sudo apt upgrade` as on any day, and answers as usual. Then the
session runs the same three commands again, plus:

```bash
grep -A4 '^Start-Date' /var/log/apt/history.log | tail -8   # what this upgrade moved
```

Expected: the same start times (needrestart restarted neither unit), the same models still loaded,
and `make doctor` passing. If apt moved nothing the engines use (no `libc6` or `libstdc++6` in that
list), this half proves less. Say so in the changelog, and repeat it after the next upgrade that
moves one.

Then **[Dan]** runs `sudo reboot`. The reboot ends the Spark session: Dan starts it again
([The Spark session](../how-to/spark-session.md), *Start the session*), and starts no unit by hand.
Then:

```bash
systemctl is-active local-ai-llama-swap local-ai-brake local-ai-compose   # active, three times: started at boot
make doctor                                                                # 11 of 11; it loads the embeddings model
make status
```

Expected: as commented, and the web UI loads and answers on the phone: `tailscale serve` survives
the reboot. llama-swap preloads nothing, so each model loads on its first request.

- [ ] **Step 6: Changelog; commit** — the drills, with dates and what each showed: the brake and the
  refused loads, the engines' and the hog's `oom_score`, the swap line, the fresh clone, and the
  routine upgrade (what apt moved, and whether it touched a library the engines use) and the reboot.

```bash
git add changelog.md
git commit -m "docs(machine): 🤖 record the Phase 1 drills" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

## ⇄ Switch point — Spark → Mac

- [ ] **Dan OKs the push** of the Spark's commits (`git push`). On the Mac:
  `git switch phase-1 && git pull`.

***

### Task 17 [Mac]: close Phase 1

**Files:**

- Modify: `website/scenarios/s05-memory-critically-low.md`, `website/scenarios/s09-phone-away.md`,
  `website/scenarios/s20-web-search.md`, `website/scenarios/s23-upgrade-day.md`;
  `website/design/plan.md` and `changelog.md` if the forward look changes anything

- [ ] **Step 1: Scenario statuses** — S09 and S20: `status: verified` and `verified: YYYY-MM-DD`
  (the dates from Task 14). S05: `status: built`, with a line saying Phase 1's brake unloads on-demand
  models first and that the idle-first order and notifications arrive in Phase 2. S23:
  `status: built`, with the date Task 16 Step 5's routine upgrade and reboot passed. It becomes
  `verified`, with that day's date, only once `make upgrade-gpu` has moved the set on a real
  upgrade day, whether in Phase 1 or later. Then
  `uv run --frozen --project spark spark docs check-scenarios` passes.
- [ ] **Step 2: The docs are true** — README §Current state and `changelog.md` match what the Spark
  session recorded; README §Contents still describes the `Makefile`, `spark/` and `stack/` rows as
  they are (Task 10 brought them up to date); `make docs` is clean (the Stack page is current).
- [ ] **Step 3: Private findings** — load times, readings in context, anything tailnet-specific go to
  the vault's `zettelkasten/local-ai/` note, never to the repo.
- [ ] **Step 4: Council review** — four reviewers against `plan.md`'s Phase 1 and this plan: goal-fit
  and scenarios; reliability; security and simplicity; toolstack. Fix what they find, one commit per
  fix.
- [ ] **Step 5: Forward look** — what did Phase 1 teach that changes Phase 2 onward? Readings against
  the budget, load times, whether llama-swap's log carries a refused start's reason, any llama-swap
  v257 surprise. Two decisions wait on this phase's readings. Whether `spark launch` gives resident
  models a lower `oom_score_adj` than on-demand ones depends on whether the engines' RSS counts
  their models (the `rss` and `oom` columns in Task 13 Step 5). Swap size and swappiness depend on
  the swap line (Task 16 Step 3).
  Update `plan.md` (with a Revisions line), the scenario pages and `changelog.md` before
  Phase 2 starts.
- [ ] **Step 6: Merge** — `make test lint docs`, then
  `git switch main && git merge --no-ff phase-1 -m "chore(repo): 🤖 merge phase 1"`. **Dan OKs**
  `git push origin main`.

***

## Phase 1 is done when

- [ ] S09 and S20 pass on the phone, and their pages read *verified* with the date.
- [ ] pi finishes a real task from the Mac through the tunnel, and from tmux as `agent`; a detached
  session survives logging out and reattaches.
- [ ] The brake, at raised thresholds, held new loads and unloaded the on-demand model first; the
  held reload was refused, `spark status` said why, and `--release` let it load again.
- [ ] A load that didn't fit was refused with needed vs available, `spark status` said why, and
  nothing was unloaded.
- [ ] A fresh clone plus `make bootstrap` and `make apply` changed nothing.
- [ ] After a routine `apt upgrade` and after a reboot, the stack served again without a hand on it,
  and `make doctor` passed (S23).
- [ ] `make test lint docs` is clean and CI is green; README §Current state and the changelog are
  true; the council review is done and the forward look applied; `phase-1` is merged to `main` with
  Dan's OK.
