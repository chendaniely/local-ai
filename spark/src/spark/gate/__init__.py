"""The gate (Phase 2a; plan.md, *The front and the gate*): admission and its queue, idle unloading, pins, sessions,
make-room and its hold, the brake's release, the residents' preload and every notification, on two Unix sockets in
front of llama-swap. `state.py` keeps what must survive its restarts; the modules of Tasks 14 to 19 join it. This file
imports nothing, so importing one of them never imports the rest."""
