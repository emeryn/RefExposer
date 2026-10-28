# Entry point for the usual tasks. Docker is the only prerequisite: nothing is installed on the host.
# make is a convenience, never a requirement: every target is one command, shown in its recipe, and
# "docker compose up -d" alone is enough to start the application.
.DEFAULT_GOAL := help
.PHONY: help up down logs build test typecheck integrations lock lock-upgrade scan scan-deps scan-published sbom

COMPOSE ?= docker compose
# Git Bash on Windows rewrites arguments that look like paths before Docker sees them
export MSYS_NO_PATHCONV = 1
export MSYS2_ARG_CONV_EXCL = *

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Start the application (http://localhost:8080)
	$(COMPOSE) up -d

down: ## Stop the application (the data volume is kept)
	$(COMPOSE) down

logs: ## Follow the logs
	$(COMPOSE) logs -f --tail=100

build: ## Rebuild the images, pulling the newest base images (applies security updates)
	$(COMPOSE) build --pull

test: ## Run the backend test suite
	docker build --quiet --target test -t refexposer-tests ./backend
	docker run --rm -v "$(CURDIR):/repo" -w /repo/backend refexposer-tests pytest -q -p no:cacheprovider

typecheck: ## Type-check and build the frontend
	docker run --rm -v "$(CURDIR)/frontend:/app" -w /app node:22-alpine npm run build

# Applications for other tools: not part of the published repository yet, so this target is not listed by
# `make help`. Add "## <description>" after the colon to publish it with them.
integrations:
	@test -f integrations/docker-compose.yml || { echo "integrations/ is not part of this checkout (not released yet)"; exit 1; }
	$(COMPOSE) -f integrations/docker-compose.yml run --rm --build packages

lock: ## Recompile backend/requirements*.txt from the .in files (pinned versions and hashes)
	sh scripts/lock.sh

lock-upgrade: ## Same, taking the newest versions the ranges in the .in files allow
	sh scripts/lock.sh --upgrade

scan: ## Supply-chain scan: dependencies, images, Dockerfiles (fails on a fixable HIGH or any CRITICAL)
	sh scripts/scan.sh

scan-deps: ## Dependency scan only (pip-audit, npm audit); no image needed
	sh scripts/scan.sh deps

scan-published: ## Scan the images as they are in the registry (what a registry scanner reports)
	sh scripts/scan.sh published

sbom: ## Write the CycloneDX bill of materials of the images to dist/sbom/
	sh scripts/sbom.sh
