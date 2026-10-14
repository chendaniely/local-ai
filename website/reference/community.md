---
title: "Community projects"
description: "What the awesome-dgx-spark list holds that this single-Spark stack can use, and where in the plan it would come in."
---

[bidual/awesome-dgx-spark](https://github.com/bidual/awesome-dgx-spark) is a curated list of
community projects for the DGX Spark and other GB10 machines (CC0-1.0). This page is a triage of
it for this stack: which entries matter here, why, and where in [the plan](../design/plan.md) they
would come in. It is not a copy. The list keeps growing, and most of it is either for clusters of
Sparks or outside this stack's goals; what was left out, and why, is at the end.

**Read 2026-10-13, at the list's commit `294eaf1` (2026-10-08), with 355 entries.** To refresh the
triage: read the list at its new head, compare it section by section with this page, add or move
entries, and change this line to the new date and commit. An entry that leaves the list stays here,
annotated with when it left.

## How to read this page

- **Every number here is the project's own,** from its README or the list's description of it.
  None of it is measured on this box.
- **Nothing on this page is installed.** Anything adopted goes through the plan first: a design
  pass, a phase task, its tests, and the sync obligations in `CLAUDE.md`.
- **The box's rules come first.** Anything that touches the kernel, an NVIDIA module, the driver or
  CUDA touches the held GPU set, which moves only on upgrade day, as one
  ([Backups, recovery and upgrades](../design/plan.md#backups-recovery-and-upgrades)). Nothing loads
  a model around llama-swap and the gate
  ([Admission and memory rules](../design/plan.md#admission-and-memory-rules)). Claude Code and
  Claude Desktop stay untouched ([Constraints](../design/plan.md#constraints)). A service that
  listens beyond 127.0.0.1 needs a reason, a ufw rule and a tailnet rule. Pins wait seven days
  after a release.
- **Flags.** *GPU set* marks an entry that changes the kernel, an NVIDIA module or the driver.
  *Binds beyond loopback* marks one that listens on every interface by default.

## Qwen3.8-27B, the coder

Phase 4's four routes for Qwen3.8-27B ([Phases](../design/plan.md#phases)) are A, llama.cpp with
MTP (the registry's coder since 2026-10-08); B, llama.cpp with a DFlash2 drafter; C, vLLM with
NVFP4; and D, SGLang with NVFP4 and DFlash2 or DSpark. Since 2026-10-14 route D is tested by hand
([The fast coder, by hand](../how-to/qwen3.8-27b-sglang.md)) and comes to the registry in Phase
4a, after Phase 2a, taking over the name `qwen3.8-27b`. These are the single-Spark reports for
those routes. [MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark](https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark)
is in the README's Reference section and the plan's Phase 4 notes already.

- [hasso5703/dgx-spark-qwen38](https://github.com/hasso5703/dgx-spark-qwen38) — route D as a
  one-command service: SGLang's release image, the NVFP4 checkpoint and a DFlash2 drafter 16 deep,
  71.4 tok/s greedy median on its own bench, seven switchable targets. The fullest record of route
  D on one box, with its pinned image digest, drafter and memory evidence. Its installer takes the
  box over (its own engines, ports, units and sudoers rules), so this stack reads it for its config
  rather than running it. The by-hand page and Phase 4a take its checkpoint, drafter and memory
  evidence, and the plan's Phase 4 notes record what it adds and corrects.
- [pangoleen/qwen3.8-27b-dgx-spark-dflash2](https://github.com/pangoleen/qwen3.8-27b-dgx-spark-dflash2)
  — route D with a draft budget of 16: 64–78 tok/s single stream on code against about 30 on chat,
  387 aggregate at 32 streams. A second report at the same draft depth.
- [Weschera/Qwen3.8-27B-NVFP4-DFlash2-DGX-Spark](https://github.com/Weschera/Qwen3.8-27B-NVFP4-DFlash2-DGX-Spark)
  — route D, digest-pinned, where a boot-time fp8_gemm autotune race decides between 42 and 33
  tok/s for the life of the process. The reason a route D measurement needs more than one boot;
  hasso5703 turns the autotuner off for the same reason.
- [gitcommit90/qwen38-27b-dgx-spark](https://github.com/gitcommit90/qwen38-27b-dgx-spark) — NVFP4
  with Inco's DFlash 2 drafter, 44.46 tok/s against DSpark's 31.95 at k=7, plus a fix guarding the
  BF16 `lm_head`. Another drafter, and a fix to check against the image route D pins.
- [0xBakeer/Qwen3.8-27B-4-bit-on-a-single-DGX-Spark](https://github.com/0xBakeer/Qwen3.8-27B-4-bit-on-a-single-DGX-Spark)
  — NVFP4 with DSpark at k=14: 75 tok/s on edit-heavy work and 29.6 on fresh text; at k=7 its edge
  over FP8 falls from 27% at one stream to 0.2% at 16. Speed depends on what is generated, so Phase 4's
  measurements use Dan's real tasks.
- [0xWhiteMage/qwen3.8-27b-kearuga-sglang-dgx-spark-dflash2](https://github.com/0xWhiteMage/qwen3.8-27b-kearuga-sglang-dgx-spark-dflash2)
  — a different target checkpoint (Kearuga) with a distilled DFlash2 drafter on SGLang, 35.33 tok/s
  at one stream and 108.90 at four. Not Qwen's own weights; noted for the drafter.
- [r0b0tlab/qwen38-27b-nvfp4-sm121-vllm](https://github.com/r0b0tlab/qwen38-27b-nvfp4-sm121-vllm)
  — route C with DFlash2: an sm_121 vLLM image and a K=8 overlay, 67.1 to 279.2 tok/s aggregate
  from one to six streams, needle-in-a-haystack 3 of 3 at 262K.
- [AEON-7/vllm-ultimate-dgx-spark](https://github.com/AEON-7/vllm-ultimate-dgx-spark) — a vLLM
  0.29.0 image compiled for sm_121a with DSpark and DFlash 2 (3.39× single stream on Qwen3.8-27B)
  and an NVFP4 KV cache. Route C from a prebuilt third-party image, which would be pinned by digest
  like any other.
- [albond/DenseSpark-Qwen3.8-27B](https://github.com/albond/DenseSpark-Qwen3.8-27B) — INT4
  AutoRound on vLLM 0.27.1 with MTP, 49.1 tok/s at one request and 260.2 at 16. Route C with a
  quantization other than NVFP4.
- [phuongncn/qwen3.6-27b-speedhack-gx10-dgx-spark](https://github.com/phuongncn/qwen3.6-27b-speedhack-gx10-dgx-spark)
  — a llama.cpp fork with DFlash drafting for Qwen3.6-27B, 7–11 to 38–40 tok/s on code. Route B's
  idea on the model before; the stack's llama.cpp has DFlash2 upstream, so this is background, not
  a build to use.

## Phase 4's other candidates

The plan's other slots ([Phases](../design/plan.md#phases), Phase 4): coders Laguna-S-2.1 and
gpt-oss-120b, lighter Qwen3.6-35B-A3B, the resident vision model, and embeddings.

- **Laguna-S-2.1.**
  [sudoingX/dgx-spark-laguna](https://github.com/sudoingX/dgx-spark-laguna) — NVFP4 with DFlash,
  25–30 tok/s at 128K, with protection against hard hangs, which matters on a box where
  overcommitting memory can freeze it.
  [Blackwellboy/laguna-s21-lab](https://github.com/Blackwellboy/laguna-s21-lab) — a 20-cell tuning
  sweep that keeps its losing cells, a 12-hour soak of 3,096 turns and a study of its thinking
  gate; a model for how Phase 4 could record its own runs.
  [r0b0tlab/laguna-s-2.1-nvfp4-sm121-vllm](https://github.com/r0b0tlab/laguna-s-2.1-nvfp4-sm121-vllm)
  — vLLM 0.25.1, DFlash K=7 at 22.2 tok/s with thinking off, pinned to a model revision.
- **gpt-oss-120b.**
  [JetBrains-Hardware/spark-setup](https://github.com/JetBrains-Hardware/spark-setup) — deploy
  scripts for Qwen, GPT-OSS 120B, Nemotron 3 NVFP4 and Gemma 4 on vLLM, with MTP holding 14.4
  tok/s at 200K context.
- **Gemma 4 26B-A4B, the resident.**
  [nabe2030/gemma4-vs-qwen35-dgx-spark](https://github.com/nabe2030/gemma4-vs-qwen35-dgx-spark) —
  Gemma 4 26B-A4B against Qwen 3.5/3.6-35B-A3B on llama.cpp, 26.5 against 58 tok/s decode, with an
  F16 thinking-mode bug isolated: Phase 4's resident comparison, run by someone else on the same
  engine.
  [shamily/gemma4-llama-dgx-spark](https://github.com/shamily/gemma4-llama-dgx-spark) — llama.cpp
  for all four Gemma 4 models, 69.9 tok/s tg128 for the 26B-A4B MoE against 11.0 for the dense
  31B.
  [r0b0tlab/gemma4-26b-a4b-nvfp4-gb10-native-cutlass](https://github.com/r0b0tlab/gemma4-26b-a4b-nvfp4-gb10-native-cutlass)
  — NVFP4 on vLLM's CUTLASS MoE backend, 260 tok/s at eight streams.
  [jethac/vllm-gemma4-nvfp4-kv-repro](https://github.com/jethac/vllm-gemma4-nvfp4-kv-repro) —
  garbage output from Gemma 4 under vLLM's NVFP4 KV cache on sm_121, traced to three open fixes; a
  caution for running Gemma on vLLM.
- **Qwen3.6-35B-A3B.**
  [r0b0tlab/qwen36-35b-a3b-nvfp4-fast-sm121-vllm](https://github.com/r0b0tlab/qwen36-35b-a3b-nvfp4-fast-sm121-vllm)
  — NVFP4 on vLLM, 80.6 tok/s at one stream to 344 at 32, GSM8K 86.73%.
  [r0b0tlab/qwen36-35b-a3b-nvfp4-sm121-vllm](https://github.com/r0b0tlab/qwen36-35b-a3b-nvfp4-sm121-vllm)
  — NVIDIA's NVFP4 checkpoint on vLLM 0.25.0, 93 tok/s single stream, 262,144-token context
  qualified. For comparison, this model's GGUF generated at 93 tok/s on this box's llama.cpp on
  2026-09-28 (`changelog.md`), on a short test rather than Phase 4's measurements.
  [localai-org/apex-quant](https://github.com/localai-org/apex-quant) — a MoE-aware mixed-precision
  GGUF recipe, Q8_0 perplexity at 21.3 GB instead of 34.4 on Qwen3.5-35B-A3B; quality per GB in
  llama.cpp's own format.
- **Embeddings.** The list has little:
  [OscarActual/gb10-llm-benchmark](https://github.com/OscarActual/gb10-llm-benchmark) benchmarks 10
  embedding models on GB10 under Ollama, with recall on a Czech retrieval task.
- **Qwen3.8-Flash-Next 176B, a model bigger than the memory.** Several entries serve it on one
  Spark by keeping its 47.7 GiB n-gram (PLE) table on the NVMe, among them hasso5703's flash lane
  (47.9 tok/s on code, at 262K),
  [tonyd2wild/Qwen3.8-Flash-Next-NVFP4-DGX-Spark](https://github.com/tonyd2wild/Qwen3.8-Flash-Next-NVFP4-DGX-Spark)
  (NVIDIA's checkpoint byte for byte, 43.9 tok/s median),
  [albond/SingleSpark-Qwen3.8-Flash-Next](https://github.com/albond/SingleSpark-Qwen3.8-Flash-Next)
  (a 101 GiB four-bit checkpoint with the table kept in memory, 43.34 tok/s at 65,536) and
  [MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark)
  (vLLM, the table packed and memory-mapped, 48.7 tok/s on prose). The cost here is disk:
  hasso5703's README gives the flash lane about 203 GB (a 126 GB checkpoint, a 29 GB image, and 48
  GB for the table it rewrites at every boot), a fifth of the 1 TB NVMe that weights, the HF cache
  and images share. It runs as the only engine on that box. *(2026-10-14: Flash-Next is on the
  plan now, by another route: llama.cpp, with Unsloth's UD-IQ3_XXS GGUF, 76.3 GiB, no container
  and no new privileges. It runs by hand now
  ([Qwen3.8-Flash-Next, by hand](../how-to/qwen3.8-flash-next.md)), and in Phase 4a as a registry
  model that loads after `spark make-room` clears the box. hasso5703's lane leaves about 17 GiB
  available, under the reserve.)*
  [GaelicThunder/moe-offload-findings](https://github.com/GaelicThunder/moe-offload-findings) is the
  counterweight: nine mostly negative findings on streaming MoE experts from NVMe.

## Evals and benchmarks

For Phase 4's metrics (footprint, cold start, time to first token, prefill at 4K/32K/64K, decode at
1/2/4/8 streams, tool-call reliability, Dan's tasks through pi) and the backlog's private eval suite
([Backlog](../design/plan.md#backlog)).

- [DanTup/spark-evals](https://github.com/DanTup/spark-evals) — a Harbor-run leaderboard of evo
  eval, aider polyglot and swe-rebench scores under the Codex and Terminus-2 agents, for four model
  configurations that fit on one Spark.
- [DG1001/local-agentic-coding-128gb](https://github.com/DG1001/local-agentic-coding-128gb) — 14
  local models under three coding harnesses on one GB10, 86 hidden tests, and the spread one model
  shows across 13 runs (47 to 85): a reason to run each task more than once.
- [Weschera/spark-bench](https://github.com/Weschera/spark-bench) — 80 scenarios in 13 domains,
  including 12 multi-turn agentic workflows, with speed weighted at 5% of its score.
- [jvr0x/dgx-spark-bench](https://github.com/jvr0x/dgx-spark-bench) — closed-loop concurrency
  sweeps with pinned recipes and per-session and aggregate tok/s; the decode-at-N-streams metric.
- [ramsred/llm-engine-benchmark](https://github.com/ramsred/llm-engine-benchmark) — vLLM, SGLang and
  TensorRT-LLM at long context on GB10 under one client, plus a study where a prefill budget of
  2048 instead of 8192 cut P95 time to first token by 26.9%.
- [Memoriant/dgx-spark-kv-cache-benchmark](https://github.com/Memoriant/dgx-spark-kv-cache-benchmark)
  — KV-cache quantization on GB10: q4_0 costs 37% of generation speed at 110K, TurboQuant's turbo3
  up to 23.6% at 32K, and prompt processing is untouched. Evidence for the backlog's quantized-KV
  lever, which the plan prices at q8_0 only.
- [mrexodia/Kolibri-1-vLLM-DGX-Spark](https://github.com/mrexodia/Kolibri-1-vLLM-DGX-Spark) —
  measured in a pi coding-agent run (42.0 tok/s, 97% prefix-cache hits at 262,144 context), the
  same harness Phase 4 measures in.
- hasso5703's `bench.sh` and `bench-matrix.sh`, the latter a frozen battery it says runs against
  any engine. Running the same battery would make Phase 4's numbers comparable with its published
  ones.
- [mARTin-B78/dgx-spark_lite-llm_llama-swap_vllm_llama-cpp_ollama](https://github.com/mARTin-B78/dgx-spark_lite-llm_llama-swap_vllm_llama-cpp_ollama)
  carries a harness pairing llama-benchy with a 69-scenario tool-calling bench (its stack is in the
  next section).

## Memory, admission and freezes

For Phase 2's gate and brake ([Admission and memory rules](../design/plan.md#admission-and-memory-rules))
and the open item on GB10 hard freezes ([Open items and risks](../design/plan.md#open-items-and-risks);
[S05](../scenarios/s05-memory-critically-low.md), [S13](../scenarios/s13-freeze-away.md)).

- **Stacks with the same shape.**
  [mARTin-B78/dgx-spark_lite-llm_llama-swap_vllm_llama-cpp_ollama](https://github.com/mARTin-B78/dgx-spark_lite-llm_llama-swap_vllm_llama-cpp_ollama)
  — llama-swap with idle eviction behind a LiteLLM gateway, over vLLM, llama.cpp and Ollama: the
  closest on the list to this stack, which has llama-swap since Phase 1, and Phase 3 decides
  whether LiteLLM joins it.
  The plan's gate never evicts one model to load another: a model too big to load beside the
  others waits for Dan's `spark make-room` (rule 4), so compare how each decides.
  [robbiemu/dgx-spark-inference](https://github.com/robbiemu/dgx-spark-inference) — SGLang services
  under systemd with a memory budget per role, refusing a launch that won't fit, on a digest-pinned
  v0.5.14-cu130 runtime: the gate's idea applied to SGLang, which route D under the gate needs.
  [jordanovski/overdrive](https://github.com/jordanovski/overdrive) — concurrent vLLM containers
  with a preflight GPU-memory admission check.
  [kshetrajna12/sparkstation](https://github.com/kshetrajna12/sparkstation) — placement by profile
  over vLLM and SGLang, unified-memory admission control and LiteLLM gateway sync.
  [Th0rgal/dgx-spark-router](https://github.com/Th0rgal/dgx-spark-router) — a stdlib-only
  OpenAI-compatible router swapping six GGUF and vLLM NVFP4 models in and out of the pool.
  [jdaln/dgx-spark-inference-stack](https://github.com/jdaln/dgx-spark-inference-stack) — on-demand
  loading, idle shutdown and one API gateway.
- **Planning a footprint.** [engineering87/sparkfit](https://github.com/engineering87/sparkfit) — a
  zero-dependency memory planner: a 128 GB budget split, a quantization advisor and a decode
  roofline at 273 GB/s. A cross-check on the registry's estimated footprints, which stay estimates
  until a soak measures them.
  [GaelicThunder/gb10-uma-inference-notes](https://github.com/GaelicThunder/gb10-uma-inference-notes)
  — five unified-memory properties measured across models and engines.
  hasso5703's `engine-preflight.sh` waits for the last engine's memory to come back before the
  next one starts, after a start piled on memory the driver still held froze a Spark
  ([sglang#40948](https://github.com/sgl-project/sglang/issues/40948)); that is the drill's
  question of how long an unloaded engine's memory takes to show in `MemAvailable`.
- **Guards and forensics.** [ivanusto/gb10-ops](https://github.com/ivanusto/gb10-ops) — host guards
  that end the offending GPU process below 3 GiB `MemAvailable` and abort a job after 240 s at
  88 °C, thresholds calibrated on one two-node box; compare with the brake's.
  [lcasarin-maker/blackbox](https://github.com/lcasarin-maker/blackbox) — forensic capture for GB10
  hangs (Xid, PCIe AER, xHCI and PSI), with a measured gap where 7 GiB held through PyTorch raised
  a cgroup's `memory.current` by 15 MiB: a cgroup limit doesn't bound GPU memory here, which
  hasso5703 reports of Docker's `--memory` too. A candidate for logging the freezes the open item
  records in the vault.
- **Clock caps.** The plan keeps a GPU clock cap for freezes unrelated to memory, only if needed
  ([Backlog](../design/plan.md#backlog), Ops).
  [tonyd2wild/DGX-Spark-Hard-Poweroff-Fix](https://github.com/tonyd2wild/DGX-Spark-Hard-Poweroff-Fix)
  diagnoses log-less hard power-offs as the embedded controller cutting power, fixed by capping the
  clock at 2200 MHz and dropping the page cache, at 5% of decode.
  [agjs/gb10-clock-cap](https://github.com/agjs/gb10-clock-cap) — at 2200 MHz, 12 °C cooler and 36%
  less GPU-rail power for 1.0% of decode and 3.9% of prefill, measured at TP=2.
  [bjarkebolding/spark-comfyui](https://github.com/bjarkebolding/spark-comfyui) caps at 2100 MHz for
  overcurrent reboots. A cap changes the box, so it gets a `changelog.md` entry when set.
- **Health checks.** [hoesing/spark-gpu-throttle-check](https://github.com/hoesing/spark-gpu-throttle-check)
  — loads the GPU with cuBLAS matmuls and flags clocks staying below 1400 MHz, a sign of a suspected
  power-delivery fault.
  [jeffrymahbuubi/dgx-spark-stress-test](https://github.com/jeffrymahbuubi/dgx-spark-stress-test) —
  a 6–24 hour burn-in at about 96% utilisation and about 85 GB resident, logging temperature and
  power every 10 s; a pattern for logging Phase 2's soak.
- **More memory.** [Entrpi/dgx-spark-serving-mode](https://github.com/Entrpi/dgx-spark-serving-mode)
  — drops GNOME and maintenance timers to `multi-user.target` for 10–15 GB more. Phase 0's bootstrap
  already made the box headless; its list of timers may still be worth reading.
  [christopherowen/dgx-spark-memory-saver](https://github.com/christopherowen/dgx-spark-memory-saver)
  — a patch to `nvidia-uvm` that packs GPU page tables on 64 KiB kernels, 3.03 GiB recovered.
  *GPU set:* a patched NVIDIA module, rebuilt for each kernel; upgrade day only, if ever.

## Visibility

For Phase 2's SwiftBar plugin and `spark doctor`
([Visibility and notifications](../design/plan.md#visibility-and-notifications)), and the backlog's
usage and utilization dashboard.

- [amer8/pulsebar](https://github.com/amer8/pulsebar) — an unofficial macOS menu-bar monitor
  streaming GPU and memory telemetry from the DGX Spark dashboard; the nearest thing on the list to
  the SwiftBar plugin.
- [hectorTSH/dgx-spark-memory-dashboard](https://github.com/hectorTSH/dgx-spark-memory-dashboard) — a
  live map of unified memory in 1 GiB tiles, loaded models against on-disk candidates ranked by free
  memory; close to what `spark status` answers.
- [MiaAI-Lab/sparkDash](https://github.com/MiaAI-Lab/sparkDash) — a dashboard with an engine probe
  for llama.cpp, vLLM and SGLang, cached against uncached prefill and daily peak tok/s.
- [joeynyc/spark-doctor](https://github.com/joeynyc/spark-doctor) — a read-only diagnostic CLI:
  power cap, memory pressure, thermal risk, CUDA 13 and sm_121 wheel mismatches, the Docker
  runtime. A list of checks to compare with `spark doctor`'s.
- [wentbackward/nv-monitor](https://github.com/wentbackward/nv-monitor) — a terminal monitor and
  Prometheus exporter in one C binary, with unified memory counted correctly under HugePages.
- [ateska/dgx-spark-prometheus](https://github.com/ateska/dgx-spark-prometheus) — a Go Prometheus
  exporter with a systemd unit and a Grafana dashboard, on port 9835. A new port; it needs the same
  ufw and tailnet review as any service.
- [parallelArchitect/sparkview](https://github.com/parallelArchitect/sparkview) and
  [chappa-ai-llc/spark-smi](https://github.com/chappa-ai-llc/spark-smi) — terminal monitors aware of
  unified memory; sparkview adds memory pressure (PSI), power rails and an anomaly logger.
- [DanTup/dgx_dashboard](https://github.com/DanTup/dgx_dashboard) — memory stats in GB and GiB,
  GPU power, and controls for Docker containers. *Binds beyond loopback* (0.0.0.0), and Docker
  access is root access here, which is why `spark` is kept out of the `docker` group.
- [antheas/spark_hwmon](https://github.com/antheas/spark_hwmon) — a hwmon kernel driver exposing
  GB10 system power, energy counters and temperatures, with PL1/PL2 power-cap controls. *GPU set:*
  a kernel module, built for the held kernel, so it moves with the set on upgrade day.

## Speech

For Phase 3's speech work ([Speech](../design/plan.md#speech);
[S07](../scenarios/s07-lecture-transcript.md)): the speech comparison of Whisper large-v3-turbo
against Parakeet, and the diarization endpoint on pyannote community-1. The backlog adds
text-to-speech and a Home Assistant voice pipeline.

- [WillIsback/whisperx-gb10](https://github.com/WillIsback/whisperx-gb10) — WhisperX transcription
  and pyannote diarization as a REST API for GB10, with an async job queue and SRT, VTT and TXT
  export, on NGC PyTorch 25.05 images.
- [Mekopa/whisperx-blackwell](https://github.com/Mekopa/whisperx-blackwell) — a prebuilt WhisperX
  image with GPU pyannote diarization, 24 minutes of audio in 62 s. It reports sm_121 as sm_90 to
  get there, a workaround to understand before trusting its output.
- [ncannings/fastconformer-trt](https://github.com/ncannings/fastconformer-trt) — an FP8 TensorRT
  engine for NeMo FastConformer: parakeet-ultra at 4,903× real time and 1.814% WER on test-clean,
  against 999× and 1.803% for stock NeMo. The Parakeet side of the comparison.
- [briancaffey/nemotron-asr-server](https://github.com/briancaffey/nemotron-asr-server) — an
  OpenAI-compatible speech-to-text server for nemotron-3.5-asr-streaming-0.6b on native NeMo, since
  the Riva path is broken on aarch64, with WebSocket streaming.
- [AEON-7/qwen3-asr-server](https://github.com/AEON-7/qwen3-asr-server) — an OpenAI
  `/v1/audio/transcriptions` server for Qwen3-ASR-0.6B on vLLM; a model the comparison doesn't
  include yet.
- **Text-to-speech, in the backlog.**
  [luka-loehr/qwen3-tts-native](https://github.com/luka-loehr/qwen3-tts-native) — Qwen3-TTS-1.7B in
  native Rust and CUDA, 94–96 ms to first audio at p95 and a 5.68 GB peak, against 2.69 s and
  108.90 GB for stock SGLang; on a shared pool, the peak is the number that matters.
  [mARTin-B78/dgx-spark-faster-qwen3-tts](https://github.com/mARTin-B78/dgx-spark-faster-qwen3-tts)
  — Faster-Qwen3-TTS as an OpenAI-compatible API with CUDA graphs and streaming.
  [Pizzaman213/fish-s2pro-gb10](https://github.com/Pizzaman213/fish-s2pro-gb10) — Fish Audio S2-Pro
  tuned for GB10.
- **A Home Assistant voice pipeline, in the backlog.**
  [jxlarrea/homeassistant-voice-recipes](https://github.com/jxlarrea/homeassistant-voice-recipes) — a
  local Wyoming pipeline with ONNX Parakeet speech recognition, speaker extraction, and Gemma 4
  26B-A4B with MTP on llama.cpp built for sm_121, the same model as the stack's resident.
- **Voice chat.** [Logos-Flux/spark-voice-pipeline](https://github.com/Logos-Flux/spark-voice-pipeline)
  (whisper.cpp, Ollama and VibeVoice, 766 ms to first audio),
  [kedarpotdar-nv/spark-realtime-chatbot](https://github.com/kedarpotdar-nv/spark-realtime-chatbot)
  and [pipecat-ai/nemotron-voicechat-dgx-spark](https://github.com/pipecat-ai/nemotron-voicechat-dgx-spark)
  (full-duplex speech to speech). Not in the plan; noted for when the backlog's audio items get
  their design pass.

## Documents, images and fine-tuning

All three are in the backlog ([Backlog](../design/plan.md#backlog)), each to get its own design
pass.

- **Documents.** [HendrikSchoettle/ragflow-dgx-spark](https://github.com/HendrikSchoettle/ragflow-dgx-spark)
  — RAGFlow v0.24.0 on aarch64 with an onnxruntime-gpu wheel built for sm_121 and multilingual OCR.
  [seitzbg/onnxruntime-gpu-sm121-aarch64](https://github.com/seitzbg/onnxruntime-gpu-sm121-aarch64)
  (a prebuilt onnxruntime-gpu 1.27.1 wheel, 7.3× over CPU) and
  [Albatross1382/onnxruntime-aarch64-cuda-blackwell](https://github.com/Albatross1382/onnxruntime-aarch64-cuda-blackwell)
  (ONNX Runtime 1.24.4's CUDA libraries for sm_121) — document and OCR tools that run on ONNX
  Runtime need a build like these on this GPU.
  [stevibe/SparklingKit](https://github.com/stevibe/SparklingKit) — OCR, transcription, grounding
  and image jobs routed to six models resident together on one Spark; an example of co-residency.
- **Images.** The backlog's image generation is ComfyUI.
  [luix93/DGX-Spark-ComfyUI](https://github.com/luix93/DGX-Spark-ComfyUI) — Docker Compose with
  SageAttention 2 built for sm_121, NVFP4, and a patch for what it calls the unified-memory
  double-VRAM bug.
  [mmartial/ComfyUI-Nvidia-Docker](https://github.com/mmartial/ComfyUI-Nvidia-Docker) — published
  aarch64 images for DGX Spark.
  [LectinDK/DGX-Spark-ComfyUI-Forge](https://github.com/LectinDK/DGX-Spark-ComfyUI-Forge) — a
  `--disable-pinned-memory` fix that holds the peak at 73–78 GB against 88. Peaks that size don't
  fit beside the residents, so image generation would come in through the gate like any model.
- **Fine-tuning.** The backlog's LoRA fine-tuning and serving.
  [natolambert/dgx-spark-setup](https://github.com/natolambert/dgx-spark-setup) — CUDA 13 training
  setup on aarch64: SDPA over flash-attn, OOM guards with swap off, and profiled SFT, DPO and LoRA
  batch ceilings.
  [albond/DGX_Spark_Unsloth_Lossless_Speedup](https://github.com/albond/DGX_Spark_Unsloth_Lossless_Speedup)
  — Triton kernels for sm_121a with a bit-identical loss curve, 7.67× on LoRA over stock Unsloth.
  [kreuzhofer/dgx-spark-unsloth-qwen3.5-training](https://github.com/kreuzhofer/dgx-spark-unsloth-qwen3.5-training)
  — BF16 LoRA on Qwen3.5-35B-A3B with per-shard loads and page-cache eviction, peak unified memory
  cut from 134 GB to 72 GB.
  [NvMayMay/nvfp4-lora-spark](https://github.com/NvMayMay/nvfp4-lora-spark) — LoRA trained on served
  NVFP4 weights, with a logprob check at serve time that catches adapters doing nothing.

## Building for sm_121, and setup guides

For any from-source build (`CLAUDE.md`'s sm_121 gotcha) and for checking this repo's runbooks
against others'.

- [NVIDIA/dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks) — NVIDIA's own
  playbooks: vLLM, SGLang, llama.cpp, NVFP4 quantization, speculative decoding. GB10 silicon is the
  same on this GIGABYTE box; anything vendor-specific comes from GIGABYTE.
- [jschmied/dgx-spark-setup-guide](https://github.com/jschmied/dgx-spark-setup-guide) — an
  end-to-end guide: llama-server's router mode, key-only SSH, DCGM monitoring, SWE-bench evals, and
  vLLM appendices.
- [Sggin1/DGX-SPARK](https://github.com/Sggin1/DGX-SPARK) — dated lab notes, among them evidence
  of sm_121a FP4 SASS; relevant to the open question of `121` against `121a-real` builds.
- [GuigsEvt/dgx_spark_config](https://github.com/GuigsEvt/dgx_spark_config) — building LLVM, Triton
  and PyTorch 2.9.1 for sm_121, about 1.5× on an 8192 FP16 GEMM over the stock cu130 build.
- [martimramos/dgx-spark-ml-guide](https://github.com/martimramos/dgx-spark-ml-guide) — 16 numbered
  PyTorch-on-GB10 failures with an error-to-fix table.
- [Th0rgal/cuda-blackwell-carry-bug](https://github.com/Th0rgal/cuda-blackwell-carry-bug) — a
  repro of a PTXAS bug on sm_121 that drops the carry flag between inline-asm blocks.
- **Prebuilt binaries and images**, each a third-party source to pin by hash or digest and hold to
  the seven-day rule: [Fulton-Engineering-Services/dgx-spark-wheels](https://github.com/Fulton-Engineering-Services/dgx-spark-wheels)
  (flash-attn, sageattention, flashinfer and torch 2.13 for GB10, each kernel-launch verified);
  [timothystewart6/vllm-gb10](https://github.com/timothystewart6/vllm-gb10) (a vLLM image for
  sm_121a with NCCL and FlashInfer built from source, `latest` promoted only after a four-model
  check; a candidate for route C beside NGC's); and
  [kyuz0/gb10-toolboxes](https://github.com/kyuz0/gb10-toolboxes) (llama.cpp, ds4 and vLLM
  containers rebuilt on four-hourly upstream polls, a moving target).
- **Recipes.** [spark-arena/sparkrun](https://github.com/spark-arena/sparkrun) launches vLLM, SGLang
  and llama.cpp from git-based recipe registries, and
  [styles01/sparkrun-recipes](https://github.com/styles01/sparkrun-recipes) pins nine one-at-a-time
  lanes for one GB10. Read for their flags, as with any installer.

***

## Left out, and why

- **Every Multi-node entry (96 of them), and the cluster parts of others.** This stack is one
  Spark. RoCE, NCCL, Ray, tensor parallelism across boxes and llama.cpp RPC cannot affect a single
  node; [`cosmicbboy-local-ai.md`](../../cosmicbboy-local-ai.md) §H lists what is excluded and
  why. Entries elsewhere that are cluster-only ([Anemll/dspark-vllm-gx10](https://github.com/Anemll/dspark-vllm-gx10),
  [BTankut/dgx-spark-sglang-moe-configs](https://github.com/BTankut/dgx-spark-sglang-moe-configs)'
  four-node figures, [dorangao/dgx-spark-toolkit](https://github.com/dorangao/dgx-spark-toolkit)
  and the like) are left out for the same reason.
- **Anything that points Claude Code at a local model**, such as the local Claude Code playbook in
  [csabakecskemeti/dgx-spark-community-playbooks](https://github.com/csabakecskemeti/dgx-spark-community-playbooks)
  and the Claude Code client notes in hasso5703's `docs/clients.md`. Claude Code stays untouched: no
  gateway, proxy, `ANTHROPIC_BASE_URL` or globally-installed MCP server in its path
  ([Constraints](../design/plan.md#constraints)). [jeremyeder/dgx-agentskills](https://github.com/jeremyeder/dgx-agentskills)
  (Claude Code skills for local serving, GPU monitoring and VMs) gets read against that constraint
  before any use.
- **One-command installers that take the box over:** hasso5703's, [botAGI/AGmind](https://github.com/botAGI/AGmind)
  (30+ containers), [a1exus/sparky](https://github.com/a1exus/sparky) (Traefik, with Cloudflare
  Tunnel ingress), [getainode/ainode](https://github.com/getainode/ainode),
  [HeKun-NVIDIA/dgx-spark-openclaw](https://github.com/HeKun-NVIDIA/dgx-spark-openclaw) and
  [Chrizz-lab/GB10-Agentig-Coding-Framework](https://github.com/Chrizz-lab/GB10-Agentig-Coding-Framework).
  This repo's bootstrap and `make apply` own the box's layout, which is what makes a rebuild
  possible ([S18](../scenarios/s18-rebuild.md)). Read them for their configs; don't run them.
- **Other operating systems:** NixOS, Rocky, CentOS bootc, Arch, kindling's read-only image and
  [timothystewart6/ubuntu-gb10](https://github.com/timothystewart6/ubuntu-gb10). DGX OS stays, and
  recovery for this box comes from GIGABYTE. ubuntu-gb10's 33-check read-only verify playbook is
  still worth reading beside `spark doctor`.
- **Fan and embedded-controller control:** [christopherowen/dgx-spark-fan-control](https://github.com/christopherowen/dgx-spark-fan-control),
  [Z841973620/dgx-spark-fan-override](https://github.com/Z841973620/dgx-spark-fan-override) and
  [djmad/Spark_Energy_Management](https://github.com/djmad/Spark_Energy_Management). Kernel modules
  against the held kernel, and firmware-level control the plan doesn't plan; its answer to heat is
  a clock cap, if needed.
- **First-time setup fixes**, such as [sjug/dgx-spark-ethernet-patch](https://github.com/sjug/dgx-spark-ethernet-patch)
  for the setup screen's ethernet check: this box was set up in Phase 0.
- **Engines outside the plan's set** — the ds4 family, TensorFold, colibri, ExLlamaV3, Atlas,
  TandemLLM, eider, veloGB10, sparklab, dgpp and other single-purpose engines. Phase 4 compares
  llama.cpp, vLLM and SGLang (TensorRT-LLM is out for now); these are read for their findings, not
  planned.
- **Outside the stack's goals:** gaming and remote desktop, Blender, 3D and video generation,
  science and HPC, robotics, password cracking, and pretraining from scratch.
- **Abliterated and uncensored builds**, of which the list has several. The plan's picks are the
  publishers' own checkpoints, with a policy-safe pick per slot.
