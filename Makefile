# Make It Lively — top-level dev targets.
#
# Run `make help` for a list of targets. Everything shells out to the
# existing tool chains (uv for backend, npm for frontend), so there's no
# hidden magic beyond coordinating both sides at once.

# Ports are pinned so CORS / Vite `strictPort` stay consistent with the app
# config. Override on the command line if you need to, e.g.
# `make dev BACKEND_PORT=9000`.
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 5173

.PHONY: help install install-backend install-frontend \
        dev dev-backend dev-frontend \
        test test-backend test-frontend \
        lint lint-backend lint-frontend \
        clean

help: ## Show this help.
	@awk 'BEGIN { FS = ":.*##"; printf "\nTargets:\n" } /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# --- install --------------------------------------------------------------

install: install-backend install-frontend ## Install backend + frontend deps.

install-backend: ## uv sync (backend).
	cd backend && uv sync --extra dev

install-frontend: ## npm install (frontend).
	cd frontend && npm install

# --- dev ------------------------------------------------------------------

# `make dev` runs backend and frontend in parallel. The trap ensures that
# Ctrl+C kills both children rather than leaving a detached process.
dev: ## Run backend + frontend with auto-reload.
	@echo "Backend → http://localhost:$(BACKEND_PORT)"
	@echo "Frontend → http://localhost:$(FRONTEND_PORT)"
	@trap 'kill 0' INT TERM; \
	  (cd backend && uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)) & \
	  (cd frontend && npm run dev -- --port $(FRONTEND_PORT)) & \
	  wait

dev-backend: ## Run backend only.
	cd backend && uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)

dev-frontend: ## Run frontend only.
	cd frontend && npm run dev -- --port $(FRONTEND_PORT)

# --- test -----------------------------------------------------------------

test: test-backend test-frontend ## Run all tests.

test-backend: ## pytest.
	cd backend && uv run pytest

test-frontend: ## vitest (headless).
	cd frontend && npm test -- --run

# --- lint / typecheck -----------------------------------------------------

lint: lint-backend lint-frontend ## Run ruff + mypy + vue-tsc.

lint-backend: ## ruff + mypy (strict).
	cd backend && uv run ruff check . && uv run mypy

lint-frontend: ## vue-tsc type check via `npm run build`.
	cd frontend && npm run build

# --- clean ----------------------------------------------------------------

clean: ## Delete generated artifacts (storage, caches, dist).
	rm -rf backend/storage
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
	rm -rf frontend/dist frontend/node_modules/.vite
