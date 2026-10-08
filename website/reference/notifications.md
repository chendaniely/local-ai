---
title: "Notifications"
description: "Every notification type on Dan's phone, with its priority."
---

This page is generated from `stack/models.yaml` by `spark docs notifications --write`: the list of
types and their priorities from there, and the words from `spark/src/spark/messages.py`. Edit
those, not this page.

Phase 2a's gate, brake and failure notifier send these to Dan's phone, through ntfy, once they are
deployed (Task 38 of the [implementation plan](../design/phase-2a.md)). Each type's priority is
`high`, `default` or `low`, or `off`, which sends none; changing one is a one-line edit in
`stack/models.yaml`'s `notifications` section, then `make apply` on the Spark. The examples share
the moments of the plan's [*What you see in
Phase 2a*](../design/plan.md#what-you-see-in-phase-2a).

| Type | Priority | When | Example |
|---|---|---|---|
| `brake_fired` | high | the brake fired: it names what it unloaded, and each further unload in the same episode sends a short follow-up; while the gate is down, the brake sends it itself, and the gate, once back, skips what the brake already sent | *Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was loading; new loads are paused. They resume by themselves after 5 min above 28 GiB available.* Then, if it must unload more: *Brake, 03:13: also unloaded Gemma and the embeddings, both idle.* Sent by the brake while the gate is down, it ends *They resume once the gate is back and memory has stayed above 28 GiB available for 5 min.* Within the hour after an automatic release, the hold waits for Dan, and it ends *It fired within an hour of the automatic release at 03:40, so they stay paused until you release them: on the Spark, `make brake-release`.* |
| `brake_needs_release` | high | a hold found after a reboot | *After the reboot, new loads are still paused from the brake at 02:58. On the Spark, `make brake-release` resumes them.* |
| `gate_down` | high | the failure notifier: the gate stopped | *The gate on brightroar stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new loads are refused until it's back. On the Spark, `make doctor` shows what's wrong.* |
| `front_down` | high | the failure notifier: the front stopped | *The front on brightroar stopped at 09:14 (it crashed; it is restarting). Requests wait for it, and any in flight were cut off. On the Spark, `make doctor` shows what's wrong.* |
| `llama_swap_down` | high | the failure notifier, or the gate when it stops answering | *The model service on brightroar stopped at 09:14. No model answers until it's back; requests wait, then are refused. On the Spark, `make doctor` shows what's wrong.* |
| `brake_down` | high | the failure notifier: the brake stopped | *The memory brake on brightroar stopped at 09:14 (it crashed; it is restarting within 2 s). earlyoom stays the backstop. On the Spark, `make doctor` shows what's wrong.* |
| `back_up` | default | the gate, once it, the front, llama-swap or the brake has run again for 60 s after a crash, so a crash loop doesn't alternate it with the `*_down` alerts | *The gate on brightroar has been running again for a minute, after 12 s down. New loads work again.* |
| `refused` | default | a request was refused, whoever asked | *Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load; python3 (chendaniely) holds 32. Free space with `spark make-room 41G` on the Spark, then try again.* A burst of the same refusal (model, key and code) goes as one: *Didn't load the coder for agent 4 more times since 09:12: same reason.* |
| `footprint_suspect` | default | an `agent` request for the model that was loading when the brake fired | *Didn't load the coder for agent: it was loading when the brake fired at 03:12. On the Spark, `spark load coder` allows it again; your own requests load it if it fits.* |
| `load_failed` | default | a start failed or passed its deadline | *The coder failed to load: the engine stopped with "failed to load model". On the Spark, `spark logs coder` shows the engine's last lines.* |
| `brake_released` | default | the gate lifted the brake's hold | *Brake released at 03:40, 64 GiB available. Reloaded Gemma and the embeddings. The coder was loading when it fired, so it loads again only when you ask.* |
| `room_hold_ended` | default | make-room's hold ended: `--done`, its time, a reboot, or used up by Dan's own loads | *make-room's hold for you ended (`--done`), all 40 GiB of it unused. Nothing to reload: the coder loads on its next request.* When it had unloaded always-loaded models: *make-room's hold for you ended (`--done`), all 40 GiB of it unused. Reloading Gemma.* |
| `resident_waiting` | default | an always-loaded model didn't fit when it was to reload: after a hold ended, at boot, after the brake's release, after `make apply`'s restart or the model service's, or after it stopped outside the gate | *Gemma didn't fit after the hold ended: it needs 32 GiB, and 9 GiB is free for a load. It loads by itself once there's room.* |
| `apply_restarted` | default | `make apply` restarted the model service | *make apply restarted the model service at 14:02. Gemma, the embeddings and whisper reloaded; the coder loads on its next request.* |
| `load_started` | low | a cold load started | *Loading the coder for pi on the Mac (24 s last time)…* |
| `loaded` | low | a load finished | *Loaded the coder in 24 s.* |
| `unloaded` | low | an idle unload, make-room or `spark unload` | *Unloaded the coder after 60 min idle.* |
| `waiting` | low | a request started waiting for memory, the brake, the load slot, or Dan (`footprint_suspect`) | *Waiting for memory: the coder for pi on the Mac, up to 30 s. It needs 41 GiB, and 18 GiB is free for a load.* |
| `pin_ended` | low | a pin's time ran out | *The pin on the coder ended at 18:00; it unloads after 60 min idle.* |
| `memory_warning` | low | available memory fell under the warn line, 28 GiB (rule 5's warning), once per fall | *Memory is getting low on brightroar: 27.4 GiB available, under the 28 GiB warning line. The brake acts at 20.* |

A change to the priority of `gate_down`, `front_down`, `llama_swap_down` or `brake_down`
needs `make install-units` after `make apply`, both on the Spark, since the failure notifier's
unit carries those four (Task 25 of the implementation plan).
