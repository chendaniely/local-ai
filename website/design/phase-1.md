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
> Phase 0's review if anything learned there changes it (see the plan's Revisions).

**Goal:** Four models served on `brightroar` through llama-swap — a resident vision chat model,
embeddings, speech-to-text and a starter coder — reachable from Open WebUI on Dan's phone (HTTPS via
`tailscale serve`) and from pi on the Mac and in tmux as `agent`, protected by a minimal memory brake
and a launch check that refuses loads that don't fit.

**Architecture:** `stack/models.yaml` is the single source of truth. The `spark` CLI renders it into
a llama-swap config, systemd units and a Compose file, validates them, and deploys them under
`/opt/local-ai/`. llama-swap (as the `spark` user, 127.0.0.1:9100, API keys required) starts every
engine through `spark launch`, which refuses a load when the brake holds or the model doesn't fit,
and marks the engine as the first thing the kernel or earlyoom should kill. `spark brake` watches
`MemAvailable` and unloads models before the box reaches the freeze band. Open WebUI and SearXNG run
under Compose (host networking, bound to 127.0.0.1) from a root-owned unit.

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
  `/var/lib/local-ai/{hf,open-webui,searxng,brake}` · `HF_HOME=/var/lib/local-ai/hf`.
- **Budget (from `stack/models.yaml`):** allocatable 102 GiB (reported; measured later) · reserve
  24 GiB · warn 28 GiB · brake 20 GiB · poll 250 ms. The static model set must fit
  `allocatable − reserve`.
- **Never put a secret in a llama-swap `cmd`** — `GET /running` shows commands unredacted. Keys reach
  llama-swap only as `${env.LLAMASWAP_KEY_*}` from `/etc/local-ai/secrets/llama-swap.env`. Never name a
  key variable `LLAMA_API_KEY` (llama-server reads it). No `--api-key` on engines (llama-swap forwards
  the client's header).
- llama-swap silently ignores unknown config keys — every config change is followed by a start and
  `GET /running`, not just `-validate`.
- **On the Spark, a key never goes on a command line.** Every user can read `/proc/*/cmdline`, and
  `agent` is the isolation boundary. curl takes the header on stdin:
  `curl -H @- … <<<"Authorization: Bearer $SPARK_API_KEY"`. Code reads keys from the environment.
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
    monkeypatch.setattr(launch.os, "execvp", lambda f, a: calls.update(file=f, argv=a))
    code = launch.main_launch(["coder", "--", "/bin/engine", "--port", "5800"], registry=FIXTURE, state=tmp_path)
    assert code == 0 and calls == {"oom": True, "file": "/bin/engine", "argv": ["/bin/engine", "--port", "5800"]}


def test_launch_refuses_with_exit_3(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 30.0))
    monkeypatch.setattr(launch.os, "execvp", lambda f, a: pytest.fail("must not exec"))
    code = launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert code == 3
    assert "spark: not starting coder: needs ~28 GiB" in capsys.readouterr().err


def test_launch_unknown_model_is_a_usage_error(tmp_path):
    assert launch.main_launch(["nope", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path) == 2


def test_a_refusal_is_kept_for_spark_status_until_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: None)
    monkeypatch.setattr(launch.os, "execvp", lambda f, a: None)
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
oom_score, so without this a user's job could be chosen instead.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from spark import paths
from spark.admission import admit
from spark.hold import read_hold
from spark.memory import read_meminfo
from spark.registry import load_registry

REFUSAL = "last-refusal.json"


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
    os.execvp(cmd[0], cmd)
    return 0  # reached only when execvp is replaced in tests


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

**Files:**
- Create: `stack/models.yaml`, `stack/templates/local-ai-llama-swap.service`,
  `stack/templates/local-ai-brake.service`, `stack/templates/local-ai-compose.service`,
  `stack/templates/local-ai-pull.service`, `stack/templates/compose.yaml`,
  `stack/templates/searxng-settings.yml`, `spark/src/spark/render.py`, `spark/tests/test_render.py`,
  `spark/tests/fixtures/versions.yaml`
- Modify: `spark/src/spark/versions.py` (optional `image` field, commit pins),
  `spark/tests/test_versions.py`, `stack/versions.yaml`, `spark/src/spark/cli.py`

**Interfaces:**
- Consumes: Tasks 1 and Phase 0's `load_versions`, `Component`.
- Produces: `RenderError(ValueError)`; `model_path(source, file) -> str`;
  `engine_cmd(model, registry) -> list[str]`; `llama_swap_config(registry) -> dict`;
  `render(registry, versions, registry_text: str, templates: Path = TEMPLATES) -> dict[str, str]`
  (relative path → content); CLI `spark render --out DIR [--registry P] [--versions P]`.
  Constants: `DEPLOY="/opt/local-ai"`, `HF_HOME="/var/lib/local-ai/hf"`,
  `SPARK_BIN="/opt/local-ai/app/.venv/bin/spark"`,
  `KEY_ENVS=("LLAMASWAP_KEY_DAN_MAC","LLAMASWAP_KEY_AGENT","LLAMASWAP_KEY_OPENWEBUI","LLAMASWAP_KEY_SPARK")`.

- [ ] **Step 1: `versions.py` gains an optional image name and commit pins** — add
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
      ENABLE_SIGNUP: "false"
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

- [ ] **Step 5: Run them and watch them fail** — `uv run --frozen --project spark pytest spark/tests/test_render.py` → FAIL.

- [ ] **Step 6: Implement**

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

- [ ] **Step 7: Run the tests — they pass.** `uv run --frozen --project spark pytest spark/tests`

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
    agent's own key sits in its `~/.secrets`, written there by Dan straight from the service secrets
    and never displayed. From the agent's clone:
    `uv run --frozen --project spark spark clients pi --write`. Work inside tmux: `tmux new -As work`,
    then `pi`; `Ctrl-b d` detaches, and `tmux attach -t work` picks it up after logging back in.

- [ ] **Step 7: `website/how-to/deploy.md`** (front matter `title: "Deploy the stack"`,
  `description: "The first deploy on the Spark, the web UI over tailscale serve, and every later change."`):
  - **Before the first deploy.** Phase 0 is done (bootstrap, secrets), the engines are installed
    (the Phase 1 plan, Task 10), and `id -nG` lists `spark-admin` and `adm`. If it doesn't, the
    session predates bootstrap: log out, `tmux kill-server`, log back in.
  - **First deploy.** `make apply` renders into `/opt/local-ai/etc`, syncs the app into
    `/opt/local-ai/app`, and reports that the units aren't installed yet. Once: `make install-units`
    (sudo). Then `make pull` (the model files, downloaded by the `spark` user; `make logs s=pull` in
    another pane shows progress), `systemctl start local-ai-llama-swap local-ai-brake local-ai-compose`
    (no sudo: the polkit rule covers `local-ai-*`), and `make status`.
  - **The web UI.** `sudo tailscale serve --bg --https=443 http://127.0.0.1:3000` — it survives
    reboots. `tailscale serve status` shows the address; it names your tailnet, so read it privately.
    On the first visit, create an account: the first one becomes the admin, and signup is otherwise
    closed. To undo: `sudo tailscale serve reset`, then serve again.
  - **Every later change.** `git pull`, `make apply-dry-run`, `make apply`. If the llama-swap config
    changed while models are loaded, apply changes nothing and says so; run it again when they're
    idle, or `make apply-now` to restart llama-swap anyway.
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

## ⇄ Switch point — Mac → Spark

- [ ] `make test lint docs` is clean on the Mac, and every `<paste>` in `stack/models.yaml` is a real
  40-hex revision.
- [ ] **Dan OKs the push:** `git push -u origin phase-1`. `gh run watch` — CI is green.

***

### Task 10 [Spark]: the engines, at their pins

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
found. Keep the clone: `samples/jfk.wav` is Task 12's speech test.

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

### Task 11 [Spark + Dan]: deploy the config, pull the models

- [ ] **Step 1 [Spark]: render and deploy** — `make apply-dry-run`, then `make apply`.
  Expected: every file under `/opt/local-ai/etc` and the app are listed as new; `llama-swap -validate`
  prints `config is valid: 4 model(s)`; `uv sync` builds `/opt/local-ai/app/.venv`; each unit is
  reported as not installed yet.

- [ ] **Step 2 [Dan]: install the units** — `make install-units` (asks for sudo once). Check:
  `systemctl list-unit-files 'local-ai-*'` shows llama-swap, brake and compose `enabled`, and pull
  `linked`.

- [ ] **Step 3 [Spark]: pull the model files** — `make pull`. About 40 GB, downloaded by the `spark`
  user; it prints nothing until it finishes, so follow it with `journalctl -fu local-ai-pull` in
  another tmux pane.
  Expected: five `pull: … → /var/lib/local-ai/hf/hub/models--…/snapshots/<revision>/<file>` lines
  and exit 0. Each path is the one the rendered config hands its engine — compare with
  `grep -o '/var/lib/local-ai/hf/hub/[^ ]*' /opt/local-ai/etc/llama-swap.yaml`. A `FAILED` line means
  a wrong file name or revision: fix `stack/models.yaml`, commit, `make apply`, `make pull` again.
  Then `df -h /`, and note the space left for the changelog.

***

### Task 12 [Spark + Dan]: start the stack, smoke-test it, first footprint readings

- [ ] **Step 1 [Dan]: your key on the Spark** — the checks below use `SPARK_API_KEY`, with the same
  value as on the Mac (one key per person, not per machine). From the Mac, without displaying it:
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

- [ ] **Step 3: One request per model, with memory readings** — from nothing loaded, one at a time.
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

- [ ] **Step 4: What runs, and as whom**

```bash
make status
ps -o user=,oom_score_adj=,comm= -C llama-server,whisper-server
```

Expected: four models loaded (three resident, the coder on demand), the brake off, and the headroom
before the brake; every engine runs as `spark` with `oom_score_adj` 1000. `spark launch` set that,
so the engines are the first processes the kernel or earlyoom would kill.

- [ ] **Step 5: Footprints** — a model's footprint is about *before − lowest*. Where a reading is
  above the registry's estimate (19, 1.5, 3 and 29 GiB), raise that model's `footprint_gib` to the
  reading, rounded up, and leave `footprint_measured: false` — Phase 2 measures at full context
  after a soak. Then `make apply`: only `models.yaml` changed, so only the brake restarts.

- [ ] **Step 6: Changelog, README; commit** — `changelog.md`: units installed and running, the model
  files pulled and the disk space left, the four readings and load times. `README.md` §Current state:
  what runs, on which ports (127.0.0.1 only), which models.

```bash
git add stack/models.yaml changelog.md README.md
git commit -m "docs(machine): 🤖 record the first deploy and footprint readings on brightroar" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 13 [Dan]: the web UI on the phone (S09, S20)

- [ ] **Step 1: Serve it on the tailnet** — `sudo tailscale serve --bg --https=443 http://127.0.0.1:3000`,
  then `tailscale serve status`. Read the output privately: the HTTPS address names the tailnet.
- [ ] **Step 2: The first account** — on the phone with Tailscale on, open the address and create an
  account; the first one becomes the admin. In a private tab, confirm that a second signup is refused.
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

### Task 14 [Dan + Spark]: pi on the Mac, and as `agent` in tmux

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

- [ ] **Step 2 [Dan, on the Spark]: Node, and the agent's key**

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh
less /tmp/nodesource_setup.sh                     # read what it does before running it as root
sudo bash /tmp/nodesource_setup.sh && sudo apt-get install -y nodejs
node --version                                    # v22.19 or later
sudo bash -c '. /etc/local-ai/secrets/llama-swap.env; umask 077; printf "export SPARK_API_KEY=%s\n" "$LLAMASWAP_KEY_AGENT" > /home/agent/.secrets; chown agent:agent /home/agent/.secrets'
```

The last line copies the agent's own llama-swap key into its home without displaying it.

- [ ] **Step 3 [Dan, as `agent`]: pi and uv for the agent** — `sudo -iu agent`, then:

```bash
grep -q '\.secrets' ~/.bashrc || sed -i '1i [ -f ~/.secrets ] && . ~/.secrets' ~/.bashrc
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g --prefix ~/.local --ignore-scripts @earendil-works/pi-coding-agent@0.85.1
git clone --branch phase-1 https://github.com/chendaniely/local-ai ~/work/local-ai
exit
```

Then, in a fresh login as `agent` (`ssh agent@brightroar`), so the key and `~/.local/bin` load:

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

- [ ] **Step 4 [Spark]: Changelog, README; commit** — record Node 22 from NodeSource, pi 0.85.1 and uv
  for `agent`, the agent's key (by reference), the Mac's pi pinned to 0.85.1, and the S09 and S20
  dates from Task 13.

```bash
git add changelog.md README.md
git commit -m "docs(machine): 🤖 record pi for agent and the web UI on the tailnet" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

***

### Task 15 [Spark + Dan]: drills — the brake, a load that doesn't fit, a fresh clone

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
  brake's warn line (28), below what the coder needs (29 plus the 24 GiB reserve):

```bash
coder() { curl -s -o /dev/null -w '%{http_code}\n' -H @- -H 'Content-Type: application/json' \
  http://127.0.0.1:9100/v1/chat/completions \
  -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  <<<"Authorization: Bearer $SPARK_API_KEY"; }
curl -fsS -X POST -H @- http://127.0.0.1:9100/api/models/unload/qwen3.6-35b-a3b <<<"Authorization: Bearer $SPARK_API_KEY"; echo
a=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
python3 -c "import time; x = b'\x01' * ($((a - 45)) << 30); time.sleep(900)" > /dev/null 2>&1 & hog=$!
sleep 30; make status          # ~45 GiB available; three residents loaded; brake off
coder                          # not 200
make status                    # refused  qwen3.6-35b-a3b at …: needs ~29 GiB, 45 GiB available (24 GiB reserve kept)
kill "$hog"; sleep 5; coder    # 200 once the memory is back
```

Expected: as commented, with the three residents loaded throughout.

- [ ] **Step 4: A fresh clone reproduces the deploy** — first, **Dan OKs pushing** the Spark's
  commits (`git push`), so the clone has everything. Then:

```bash
fresh=$(mktemp -d) && git clone --branch phase-1 https://github.com/chendaniely/local-ai "$fresh/local-ai"
cd "$fresh/local-ai"
make bootstrap           # [Dan], in this directory: sudo; every step is already in place
make apply               # apply: nothing to change
cd ~ && rm -rf "$fresh"
```

Expected: bootstrap finishes without error, and apply prints `apply: nothing to change` — the
running stack is exactly what the repo describes.

- [ ] **Step 5: Changelog; commit** — the three drills, with dates and what each showed.

```bash
git add changelog.md
git commit -m "docs(machine): 🤖 record the Phase 1 drills" \
  -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

## ⇄ Switch point — Spark → Mac

- [ ] **Dan OKs the push** of the Spark's commits (`git push`). On the Mac:
  `git switch phase-1 && git pull`.

***

### Task 16 [Mac]: close Phase 1

**Files:**
- Modify: `website/scenarios/s05-memory-critically-low.md`, `website/scenarios/s09-phone-away.md`,
  `website/scenarios/s20-web-search.md`; `website/design/plan.md` and `changelog.md` if the forward
  look changes anything

- [ ] **Step 1: Scenario statuses** — S09 and S20: `status: verified` and `verified: YYYY-MM-DD`
  (the dates from Task 13). S05: `status: built`, with a line saying Phase 1's brake unloads on-demand
  models first and that the idle-first order and notifications arrive in Phase 2. Then
  `uv run --frozen --project spark spark docs check-scenarios` passes.
- [ ] **Step 2: The docs are true** — README §Current state and `changelog.md` match what the Spark
  session recorded; `make docs` is clean (the Stack page is current).
- [ ] **Step 3: Private findings** — load times, readings in context, anything tailnet-specific go to
  the vault's `zettelkasten/local-ai/` note, never to the repo.
- [ ] **Step 4: Council review** — four reviewers against `plan.md`'s Phase 1 and this plan: goal-fit
  and scenarios; reliability; security and simplicity; toolstack. Fix what they find, one commit per
  fix.
- [ ] **Step 5: Forward look** — what did Phase 1 teach that changes Phase 2 onward? Readings against
  the budget, load times, whether llama-swap's log carries a refused start's reason, any llama-swap
  v257 surprise. Update `plan.md` (with a Revisions line), the scenario pages and `changelog.md` before
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
- [ ] `make test lint docs` is clean and CI is green; README §Current state and the changelog are
  true; the council review is done and the forward look applied; `phase-1` is merged to `main` with
  Dan's OK.
