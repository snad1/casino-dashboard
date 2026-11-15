# One entry point for every package. Run from the repo root.
#   make install    install backend, agent and frontend dependencies
#   make check      lint + type + test + security (what CI runs)
#   make security   scanners only; see docs/security/scans.md
#   make e2e        Playwright against a production build (downloads browsers once)
#   make dev-api    API on http://localhost:8010 with reload (creates local dev secrets once)

SHELL       := /bin/bash
.SHELLFLAGS := -eo pipefail -c

# uv uses each package's own .venv; an activated venv elsewhere only adds warnings.
unexport VIRTUAL_ENV

UV_BACKEND := uv run --project backend
UV_AGENT   := uv run --project agent
PNPM       := cd frontend && pnpm

BANDIT_VERSION    := 1.9.4
PIP_AUDIT_VERSION := 2.10.1
SEMGREP_VERSION   := 1.185.0
SCAN_IMAGE        := casino-dashboard-api:scan

.PHONY: install lint type test check security bandit pip-audit pnpm-audit semgrep gitleaks hadolint image-scan e2e dev-secrets dev-api

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

check: lint type test security

security: bandit pip-audit pnpm-audit semgrep gitleaks hadolint image-scan

bandit:
	uvx bandit==$(BANDIT_VERSION) -ll -q -r backend/app agent/agent

pip-audit:
	cd backend && uv export --frozen --no-dev --no-hashes --no-emit-project -q \
		| uvx pip-audit==$(PIP_AUDIT_VERSION) --strict --progress-spinner off -r /dev/stdin
	cd agent && uv export --frozen --no-dev --no-hashes --no-emit-project -q \
		| uvx pip-audit==$(PIP_AUDIT_VERSION) --strict --progress-spinner off -r /dev/stdin

pnpm-audit:
	$(PNPM) audit --prod --audit-level high

semgrep:
	uvx semgrep==$(SEMGREP_VERSION) scan --config p/python --config p/owasp-top-ten \
		--error --metrics off --quiet --no-git-ignore backend/app agent/agent frontend/src

gitleaks:
	gitleaks git --redact --no-banner --config .gitleaks.toml .
	gitleaks dir --redact --no-banner --config .gitleaks.toml .

hadolint:
	hadolint backend/Dockerfile

image-scan:
	@docker info >/dev/null 2>&1 || { echo "image-scan needs Docker running (start Docker Desktop)" >&2; exit 1; }
	docker build -q -t $(SCAN_IMAGE) backend >/dev/null
	trivy image --quiet --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed $(SCAN_IMAGE)

e2e:
	$(PNPM) exec playwright install chromium
	cd frontend && NODE_ENV=test pnpm e2e

# Random local-only secrets, written once; the api refuses to start without them.
dev-secrets:
	@mkdir -p backend/.secrets && chmod 700 backend/.secrets
	@for s in session_secret postgres_password redis_dashboard_password; do \
		f=backend/.secrets/$$s; \
		[ -s $$f ] || { (umask 077; openssl rand -base64 36 | tr -d '\n' > $$f); }; \
	done

dev-api: dev-secrets
	cd backend && APP_ENV=local SECRETS_DIR=.secrets PUBLIC_HOST=localhost \
		uv run uvicorn app.main:create_app --factory --reload --port 8010
