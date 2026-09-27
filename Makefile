# local-ai — the front door. `make help` lists the targets.
# Kept to GNU make 3.81 features so it runs on macOS as well as Ubuntu.

UV      := uv run --frozen --quiet --project spark
SPARK   := $(UV) spark
.DEFAULT_GOAL := help
.PHONY: help test hooks lint docs bootstrap bootstrap-dry-run hold-gpu hold-gpu-dry-run upgrade-gpu upgrade-gpu-dry-run apply apply-dry-run apply-now install-units install-units-dry-run pull status brake-release logs tunnel clients doctor

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-22s %s\n", $$1, $$2}'

test: ## Run the unit and render tests
	uv run --frozen --project spark pytest spark/tests

hooks: ## Turn on the leak-check hooks in this clone (needs gitleaks 8.19+ and your denylist)
	@command -v gitleaks >/dev/null || { echo "install gitleaks first (website/how-to/leak-guards.md)"; exit 1; }
	@gitleaks git --help >/dev/null 2>&1 || { echo "this gitleaks has no 'git' command: the hooks need 8.19 or later (Ubuntu's archive ships 8.16); see website/how-to/leak-guards.md"; exit 1; }
	@$(SPARK) leakcheck --message /dev/null # the hooks' own denylist check: it exists, has terms, parses
	git config core.hooksPath .githooks
	@echo "leak-check hooks on for this clone"

lint: ## Shellcheck the hooks and host scripts
	shellcheck .githooks/pre-commit .githooks/commit-msg stack/host/bootstrap.sh

docs: ## Regenerate the Stack page, check scenario pages, render the site
	$(SPARK) docs stack --write
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
	sudo bash stack/host/bootstrap.sh --upgrade-gpu

apply: ## On the Spark: render, validate, deploy; won't restart llama-swap under loaded models
	$(SPARK) apply

apply-dry-run: ## On the Spark: show what apply would change
	$(SPARK) apply --dry-run

apply-now: ## On the Spark: apply even if restarting llama-swap stops loaded models
	$(SPARK) apply --now

install-units-dry-run: ## On the Spark: show what make install-units would install as root; changes nothing
	bash stack/host/bootstrap.sh --install-units --dry-run

install-units: ## On the Spark (Dan; sudo): install root's copies of the units and Compose project that make apply staged
	trap 'sudo -k' EXIT INT TERM HUP; sudo bash stack/host/bootstrap.sh --install-units

pull: ## On the Spark: download the model files at their pinned revisions (as the spark user)
	t=$$(date '+%Y-%m-%d %H:%M:%S'); systemctl start local-ai-pull.service; s=$$?; journalctl -u local-ai-pull.service --since "$$t" --no-pager; exit $$s

status: ## On the Spark: what's loaded, memory before the brake, the brake, the last refusal
	$(SPARK) status

brake-release: ## On the Spark (spark-admin): lift the brake's hold, once memory is back
	$(SPARK) brake --release

logs: ## On the Spark: make logs s=llama-swap|brake|pull|compose|open-webui|searxng
	@case "$(s)" in "") echo "usage: make logs s=llama-swap|brake|pull|compose|open-webui|searxng" >&2; exit 2 ;; open-webui|searxng) journalctl CONTAINER_NAME=local-ai-$(s)-1 -n 100 --no-pager ;; *) journalctl -u local-ai-$(s).service -n 100 --no-pager ;; esac

tunnel: ## On the Mac: forward the Spark's llama-swap to 127.0.0.1:9100 (Ctrl-C closes it)
	ssh -N -L 9100:127.0.0.1:9100 $${SPARK_SSH_HOST:-brightroar}

clients: ## Add the Spark provider to pi on this machine
	$(SPARK) clients pi --write

doctor: ## On the Spark: Phase 0's guardrails and the stack, checked in one pass
	$(SPARK) doctor
