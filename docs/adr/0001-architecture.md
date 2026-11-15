# 0001: Architecture of the casino dashboard

- Status: accepted
- Date: 2026-11-15

## Context

The bots run on one Linux VPS under systemd `--user`, write every bet to SQLite and are managed over SSH with `*ctl` commands. The existing web app reads the Telegram bot's databases, is not real time, and cannot control the bots. The owner wants a real-time, trading-style dashboard reachable from a phone, with control buttons for every `*ctl` action, a web terminal, roles and invites, alerts, and no measurable cost to betting speed.

## Decision

**Daemon.** The bot keeps SQLite on its hot path. It gains an optional event publisher: the betting thread appends to a bounded in-memory deque and a separate thread drains it into Redis Streams (`XADD MAXLEN ~`). Any failure drops events. The publisher is off by default and can be turned off live.

**Services.** One Docker Compose stack per casino service, each on its own subdomain:

| Service | Role |
|---|---|
| `timescale` | Postgres 16 + TimescaleDB: history, users, roles, sessions, audit, alerts, comments, outbox |
| `redis` | Event streams from the daemon; ACL user that may only `XADD` on its own keys |
| `api` | FastAPI (async): REST, WebSocket fan-out, auth, RBAC |
| `ingest` | Tails the bot's SQLite read-only by `bets.id` watermark into a Timescale hypertable |
| `caddy` | TLS, security headers, serves the SPA, proxies `/api` and `/ws` |

**Frontend.** SvelteKit with the static adapter (SPA), Tailwind, TradingView lightweight-charts, xterm.js; installable as a PWA.

**Paths.**

- Live: daemon → Redis stream → one consumer per stream in `api` → aggregator at 4 to 10 Hz → WebSocket. No per-client database polling.
- History: `ingest` → Timescale with continuous aggregates. The live stream is lossy and is never the source of history.
- Control: `api` → unix socket → host control agent (systemd `--user`) → allowlisted commands built as argv lists, never a shell. The web terminal goes through the same agent.

**Notifications.** Brevo email, Telegram, Web Push.

**Adapters.** Service-specific code lives in `backend/app/adapters/<service>/` and `agent/agent/services/<service>.py`; core code depends only on a `ServiceAdapter` protocol.

## Constraints

- The bet path stays O(1) and non-blocking: no I/O, logging, locking or network work per bet.
- Every I/O boundary has named controls and a negative test (TLS + strict CSP + httpOnly SameSite cookies + CSRF in the browser; Origin allowlist and one-time ticket on WebSockets; peer-credential and token checks on the agent socket; strict pydantic models on every input).
- No default credentials; services refuse to start with a missing or weak secret.
- Containers run with CPU and memory caps and a lower CPU weight than the bots; the agent runs at low CPU and IO priority.
- Event and agent messages are versioned; schemas and golden fixtures live in `contracts/`.

## Consequences

- Betting speed is protected by design and measured by a benchmark gate before the dashboard depends on the stream.
- Two stores hold related data (SQLite on the host, Timescale in the stack); reconciliation checks counts and sums between them.
- The agent is a privileged component and gets the strictest review: allowlist, validators, fuzz tests, audit log.
- The old web keeps running until the new dashboard proves parity, then retires.
