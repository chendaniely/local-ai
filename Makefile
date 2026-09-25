# local-ai — the front door. `make help` lists the targets.
# Kept to GNU make 3.81 features so it runs on macOS as well as Ubuntu.

UV      := uv run --frozen --quiet --project spark
SPARK   := $(UV) spark
.DEFAULT_GOAL := help
.PHONY: help test hooks lint docs bootstrap bootstrap-dry-run hold-gpu hold-gpu-dry-run

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-20s %s\n", $$1, $$2}'

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
	sudo bash stack/host/bootstrap.sh

hold-gpu-dry-run: ## Print what re-holding the GPU set would do; changes nothing
	bash stack/host/bootstrap.sh --hold-gpu --dry-run

hold-gpu: ## Re-hold the GPU set and nothing else — upgrade day (Dan; asks for sudo once)
	sudo bash stack/host/bootstrap.sh --hold-gpu
