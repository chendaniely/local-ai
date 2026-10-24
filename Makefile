# local-ai — the front door. `make help` lists the targets.
# Kept to GNU make 3.81 features so it runs on macOS as well as Ubuntu.

UV      := uv run --frozen --quiet --project spark
SPARK   := $(UV) spark

# make test's memory cap (Dan's decision, 2026-10-15). On the Spark, where SPARK_OPT exists, pytest runs in a user scope
# capped at TEST_CAP_GIB with no swap, so the kernel kills only a runaway run, never the models' memory; a session whose
# user manager doesn't answer gets a line saying so, never an uncapped run. Elsewhere (the Mac, CI) it runs as before.
# The size is spark/tests/memory_cap.py's CAP_GIB, which says why; spark/tests/conftest.py refuses an uncapped run on
# the Spark. ARGS, when given, replaces spark/tests: make test ARGS="spark/tests/test_x.py -k name".
SPARK_OPT    = /opt/local-ai
TEST_CAP_GIB = $(shell sed -n 's/^CAP_GIB = \([0-9][0-9]*\)$$/\1/p' spark/tests/memory_cap.py)
TEST_PYTEST  = uv run --frozen --project spark pytest $(if $(ARGS),$(ARGS),spark/tests)
TEST_SCOPE   = systemd-run --user --scope --quiet -p MemoryMax=$(TEST_CAP_GIB)G -p MemorySwapMax=0 --
TEST_MANAGER = systemctl --user show --property=Version --value >/dev/null 2>&1 || { echo "make test: your systemd user manager isn't answering, and on the Spark the tests run only under its $(TEST_CAP_GIB) GiB cap. Log in again, so the user manager starts, then run make test again." >&2; exit 1; }
.DEFAULT_GOAL := help
.PHONY: help test hooks lint docs bootstrap bootstrap-dry-run hold-gpu hold-gpu-dry-run upgrade-gpu upgrade-gpu-dry-run apply apply-dry-run apply-now restart-front install-units install-units-dry-run pull status brake-release logs tunnel clients doctor fast-coder flash-next

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-22s %s\n", $$1, $$2}'

test: ## Run the unit and render tests, capped on the Spark; ARGS="…" runs some
	$(if $(wildcard $(SPARK_OPT)),@$(TEST_MANAGER))
	$(if $(wildcard $(SPARK_OPT)),$(TEST_SCOPE) $(TEST_PYTEST),$(TEST_PYTEST))

hooks: ## Turn on the leak-check hooks in this clone (needs gitleaks 8.19+ and your denylist)
	@command -v gitleaks >/dev/null || { echo "install gitleaks first (website/how-to/leak-guards.md)"; exit 1; }
	@gitleaks git --help >/dev/null 2>&1 || { echo "this gitleaks has no 'git' command: the hooks need 8.19 or later (Ubuntu's archive ships 8.16); see website/how-to/leak-guards.md"; exit 1; }
	@$(SPARK) leakcheck --message /dev/null # the hooks' own denylist check: it exists, has terms, parses
	git config core.hooksPath .githooks
	@echo "leak-check hooks on for this clone"

lint: ## Shellcheck the hooks and host scripts
	shellcheck .githooks/pre-commit .githooks/commit-msg stack/host/bootstrap.sh stack/host/local-ai-notify stack/host/local-ai-agent-oom stack/host/by-hand-fast-coder stack/host/by-hand-flash-next

docs: ## Regenerate the Stack and Notifications pages, check scenario pages, render the site (Quarto: on the Mac)
	$(SPARK) docs stack --write
	$(SPARK) docs notifications --write
	$(SPARK) docs check-scenarios
	quarto render website

bootstrap-dry-run: ## Print what bootstrap would do; changes nothing
	bash stack/host/bootstrap.sh --dry-run

bootstrap: ## Host setup on the Spark (Dan; asks for sudo once)
	trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh

hold-gpu-dry-run: ## Print what re-holding the GPU set would do; changes nothing
	bash stack/host/bootstrap.sh --hold-gpu --dry-run

hold-gpu: ## Re-hold the GPU set and nothing else — upgrade day (Dan; asks for sudo once)
	trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --hold-gpu

upgrade-gpu-dry-run: ## Print what upgrade day would do to the GPU set; changes nothing
	bash stack/host/bootstrap.sh --upgrade-gpu --dry-run

upgrade-gpu: ## Upgrade day: move the GPU set as one, in tmux (Dan; asks for sudo once)
	@test -n "$$TMUX" || { echo "make upgrade-gpu: run it inside tmux (tmux new -As upgrade), so a dropped SSH session can't stop apt halfway" >&2; exit 1; }
	trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --upgrade-gpu

apply: ## On the Spark: render, validate, deploy; restarts llama-swap and the front only once nothing is in flight (a quiet minute, or drain now)
	$(SPARK) apply

apply-dry-run: ## On the Spark: show what apply would change
	$(SPARK) apply --dry-run

apply-now: ## On the Spark: apply, restarting llama-swap and the front now; asks first when the gate lists requests in flight (with the gate down it can't see them, and cuts them off without asking)
	$(SPARK) apply --now

restart-front: ## On the Spark: apply, restarting the front (and the gate) after the key digests or the key list change, once nothing is in flight (a quiet minute, or drain now)
	$(SPARK) apply --restart local-ai-front.service

install-units-dry-run: ## On the Spark: show what make install-units would install as root; changes nothing
	bash stack/host/bootstrap.sh --install-units --dry-run

install-units: ## On the Spark (Dan; sudo): install root's copies of the units and Compose project that make apply staged
	trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --install-units

pull: ## On the Spark: download the model files at their pinned revisions (as the spark user)
	t=$$(date '+%Y-%m-%d %H:%M:%S'); systemctl start local-ai-pull.service; s=$$?; journalctl -u local-ai-pull.service --since "$$t" --no-pager; exit $$s

status: ## On the Spark: room for a load, what's loaded, waiting, paused or held, recent, health (from the gate)
	$(SPARK) status

brake-release: ## On the Spark (spark-admin): lift the brake's hold through the gate, which reloads the always-loaded models it unloaded; directly if the gate isn't answering
	$(SPARK) brake --release

logs: ## On the Spark: make logs s=llama-swap|brake|pull|compose|open-webui|searxng|front|gate|notify
	@case "$(s)" in "") echo "usage: make logs s=llama-swap|brake|pull|compose|open-webui|searxng|front|gate|notify" >&2; exit 2 ;; open-webui|searxng) journalctl CONTAINER_NAME=local-ai-$(s)-1 -n 100 --no-pager ;; notify) journalctl -u 'local-ai-notify@*' -n 100 --no-pager ;; *) journalctl -u local-ai-$(s).service -n 100 --no-pager ;; esac

tunnel: ## On the Mac: forward the Spark's model API, 127.0.0.1:9100 (Ctrl-C closes it)
	ssh -N -L 9100:127.0.0.1:9100 $${SPARK_SSH_HOST:-brightroar}

clients: ## Add the Spark provider to all clients on this machine (pi, opencode, hermes)
	$(SPARK) clients pi --write
	$(SPARK) clients opencode --write
	$(SPARK) clients hermes --write

clients-pi: ## Add the Spark provider to pi only
	$(SPARK) clients pi --write

clients-opencode: ## Add the Spark provider to OpenCode only
	$(SPARK) clients opencode --write

clients-hermes: ## Add the Spark provider to Hermes only
	$(SPARK) clients hermes --write

doctor: ## On the Spark: Phase 0's guardrails and the stack, checked in one pass
	$(SPARK) doctor

# TEMPORARY: remove once Phase 4a puts the fast coder and Flash-Next in the registry (Dan's decision, 2026-10-15).
# Delete then: in this Makefile, these two targets, this comment, their .PHONY names and their scripts in lint's list;
# stack/host/by-hand-fast-coder, stack/host/by-hand-flash-next, spark/tests/test_by_hand.py, the by-hand targets'
# tests at the end of spark/tests/test_makefile.py, and the notes naming them in website/how-to/qwen3.8-27b-sglang.md
# and website/how-to/qwen3.8-flash-next.md, and the sentences naming them in website/design/usage-meter.md and
# website/design/usage-meter-counter.md. plan.md's Phase 4a lists the same, and test_by_hand.py's TEMPORARY.
# Each runs its runbook's Run it again on the Spark, in one script; SPARK_OPT stands in for /opt/local-ai, as for
# make test, and off the Spark the script refuses.
fast-coder: ## TEMPORARY, on the Spark: the fast coder on SGLang beside the stack, as its runbook's Run it again; DO=wait waits for it, DO=stop stops it
	@BY_HAND_OPT='$(SPARK_OPT)' bash stack/host/by-hand-fast-coder '$(DO)'

flash-next: ## TEMPORARY, on the Spark: stops llama-swap and runs Flash-Next at 128K (MTP=1: 64K with MTP); DO=wait waits for it, DO=stop restores the stack
	@BY_HAND_OPT='$(SPARK_OPT)' MTP='$(MTP)' bash stack/host/by-hand-flash-next '$(DO)'
