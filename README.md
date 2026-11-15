# casino-dashboard

Real-time dashboard for the casino bots: live profit, bets and sessions over WebSockets, control buttons for every `*ctl` action, a web terminal, roles and invites, email alerts and an installable phone app.

One deployment per service, each on its own subdomain (`wolfbet.<domain>`, `goated.<domain>`, ...). WolfBet first.

## Layout (built by the backlog)

| Path | Contents |
|---|---|
| `backend/` | FastAPI api and ingest worker, Alembic migrations |
| `agent/` | Host control agent: allowlisted commands, jobs, PTY |
| `frontend/` | SvelteKit (static adapter), Tailwind, lightweight-charts, xterm.js |
| `contracts/` | Event and agent-protocol schemas plus fixtures |
| `deploy/` | Compose stack, Caddyfile, install and backup scripts |
| `docs/` | ADRs, UX patterns, ops runbooks |

## Quick start

```bash
brew install uv gitleaks hadolint trivy
make install             # uv sync for backend and agent, pnpm install for frontend
make check               # lint, types, tests and security scans (Docker must be running)
make dev-api             # API on http://localhost:8010
```

`make lint type test` needs no Docker. The scanners, thresholds and how to suppress a finding are in [docs/security/scans.md](docs/security/scans.md).

Single package, from inside its directory: `cd backend && uv run pytest`, `cd agent && uv run pytest`, `cd frontend && pnpm test`.

## Branches

- `main` is the live line for the repo, never ahead of a proven build.
- `feat/casino-dashboard` carries the dashboard build until it is proven on the test server. See [docs/ops/branch-deploy.md](docs/ops/branch-deploy.md).

## Work plan

The stories live in the casino workspace at `specs/backlogs/2026-11-15-casino-dashboard.md` (epics: foundations, daemon event publisher, control surface, host agent, auth/RBAC, live pipeline, history store, UI, control UI, terminal, alerts, analytics, ops, cut-over).
