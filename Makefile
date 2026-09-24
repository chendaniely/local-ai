# local-ai — the front door. `make help` lists the targets.
# Kept to GNU make 3.81 features so it runs on macOS as well as Ubuntu.

UV      := uv run --frozen --quiet --project spark
SPARK   := $(UV) spark
.DEFAULT_GOAL := help
.PHONY: help test hooks lint

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-20s %s\n", $$1, $$2}'

test: ## Run the unit and render tests
	uv run --frozen --project spark pytest spark/tests

hooks: ## Turn on the leak-check hooks in this clone (needs gitleaks and your denylist)
	@command -v gitleaks >/dev/null || { echo "install gitleaks first (website/how-to/leak-guards.md)"; exit 1; }
	@test -f "$${LOCAL_AI_DENYLIST:-$$HOME/.config/local-ai/denylist}" || { echo "create your denylist first (website/how-to/leak-guards.md)"; exit 1; }
	git config core.hooksPath .githooks
	@echo "leak-check hooks on for this clone"

lint: ## Shellcheck the hooks and host scripts
	shellcheck .githooks/pre-commit .githooks/commit-msg
