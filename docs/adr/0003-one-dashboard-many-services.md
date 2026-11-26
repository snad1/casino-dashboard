# 0003: One dashboard for many services

- Status: accepted
- Date: 2026-11-26
- Replaces: the "one Docker Compose stack per casino service, each on its own subdomain" part of [0001](0001-architecture.md) and [0002](0002-deploy.md)

## Context

The dashboard went live for WolfBet at casino.snadstudio.com. The owner runs five more bots on the same VPS (goated, shuffle, Stake, CryptoGames, DuckDice) and wants to manage all of them from that one dashboard: switch service in the top bar, add each service's keys, watch its lanes and sessions, and update a bot after its code is pushed.

0001 and 0002 planned one stack per service on its own subdomain. With six services that means six Postgres and six Redis instances on one VPS, six Coolify apps to deploy, and six logins with six sets of invites and roles. Roles were already per service (`user_roles.service_id`), and the history tables are already keyed by a `service` column, so one database can hold every service.

## Decision

One stack serves every installed service under one login and one domain.

- **Registry.** The backend keeps one registry of known services (label, ctl name, adapter, controls, ingest file names, bet-column map). `DASHBOARD_SERVICES` lists the enabled ones; WolfBet is the default and the only one until its next service is ready.
- **Live.** One api process runs an aggregator, hub, consumer and resync per enabled service. A failure in one service never stalls another.
- **History.** One ingest process tails every enabled service's SQLite into the shared tables, each with its own file names and column map.
- **Control.** One host agent registers each bot listed in `CASINO_AGENT_SERVICES`, on a shared base for the commands every ctl has.
- **Redis.** One Redis; each bot publishes with its own ACL user, allowed `XADD` on `<service>:*` only.
- **Frontend.** One build; a top-bar switcher lists the services from `GET /services`, which shows only the services the user holds a role on.
- **Deploy.** One Coolify app. Enabling a service is an env change plus the per-service secrets, not a new stack.
- **Updates.** Each service shows when its install is behind `origin/main` and updates on a button with the rolling `<ctl> update`. An opt-in auto-update, off by default per service, runs the same rolling update after a green CI run on `main`, through an HMAC-checked GitHub webhook.

## Consequences

- One login and one set of invites and roles for every service; the switcher hides services a user has no role on.
- One Postgres and one Redis: less memory than six stacks, but one Redis restart drops live events for every service at once (history is unaffected: ingest reads SQLite).
- The api and ingest carry more load as services are enabled; their caps are reviewed when each service goes live, and the bots keep a higher CPU weight than the dashboard.
- Service-specific code still lives only in `backend/app/adapters/<service>/` and `agent/agent/services/<service>.py`; the core reads the registry instead of importing WolfBet.
- The work is tracked in `specs/backlogs/2026-11-26-multi-service-dashboard.md`, which supersedes the other-services epic of the 2026-11-15 backlog. The per-service VPS steps are in [docs/ops/services-runbook.md](../ops/services-runbook.md).
