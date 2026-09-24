# local-ai — the front door. `make help` lists the targets.
# Kept to GNU make 3.81 features so it runs on macOS as well as Ubuntu.

UV      := uv run --frozen --quiet --project spark
SPARK   := $(UV) spark
.DEFAULT_GOAL := help
.PHONY: help test

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-20s %s\n", $$1, $$2}'

test: ## Run the unit and render tests
	uv run --frozen --project spark pytest spark/tests
