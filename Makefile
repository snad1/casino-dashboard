# One entry point for every package. Run from the repo root.
#   make install   install backend, agent and frontend dependencies
#   make check     lint + type + test for all three (what CI and hooks rely on)
#   make e2e       Playwright against a production build (downloads browsers once)

UV_BACKEND := uv run --project backend
UV_AGENT   := uv run --project agent
PNPM       := cd frontend && pnpm

.PHONY: install lint type test check e2e

install:
	uv sync --project backend
	uv sync --project agent
	$(PNPM) install --frozen-lockfile

lint:
	$(UV_BACKEND) ruff check backend
	$(UV_BACKEND) ruff format --check backend
	$(UV_AGENT) ruff check agent
	$(UV_AGENT) ruff format --check agent
	$(PNPM) lint

type:
	cd backend && uv run mypy app tests
	cd agent && uv run mypy agent tests
	$(PNPM) check

test:
	cd backend && APP_ENV=testing uv run pytest
	cd agent && APP_ENV=testing uv run pytest
	cd frontend && NODE_ENV=test pnpm test

check: lint type test

e2e:
	$(PNPM) exec playwright install chromium
	cd frontend && NODE_ENV=test pnpm e2e
