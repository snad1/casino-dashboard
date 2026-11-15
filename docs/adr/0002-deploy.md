# 0002: Deployment stack on the bot host

- Status: accepted
- Date: 2026-11-15

## Context

The dashboard runs on the same VPS as the bots. It must not take CPU or IO the bots need, must not expose anything but HTTPS, and must not hold a default or committed secret.

## Decision

**Docker Compose, one stack per service** (`deploy/compose.yml`), five containers: `timescale`, `redis`, `api`, `ingest`, `caddy`.

**Image for Postgres.** `timescale/timescaledb:<version>-pg16`, not `timescaledb-ha`. The plain image uses the official postgres entrypoint, which reads `POSTGRES_PASSWORD_FILE` from a Docker secret; the HA image does not read password files, and its replication tooling has no use on one host.

**Networks.** `internal` (`internal: true`) joins every service and has no route out. `edge` gives Caddy its public ports. `hostlink` exists only so Redis can publish `127.0.0.1:6379` for the bot daemon, which runs on the host outside Compose. Nothing else publishes a port.

**Resource caps.** Every container has `cpu_shares: 256` against the bots' default 1024, plus hard `cpus` and `mem_limit`: timescale 1.0/1g (shared_buffers 256MB), api 1.0/512m, ingest 0.5/256m, redis 0.25/320m (maxmemory 256mb), caddy 0.25/128m.

**Container hardening.** `no-new-privileges` everywhere. `api` and `ingest` run as uid 10001, read-only root filesystem, `/tmp` on tmpfs, all capabilities dropped. Redis and Caddy are read-only too; Caddy keeps only `NET_BIND_SERVICE`.

**Secrets.** Three files under `deploy/secrets/` (git-ignored, mode 600), passed as Compose secrets. Postgres reads its file directly. Redis passwords never appear in clear: an entrypoint hashes each secret into the ACL file under `/tmp` and refuses to start when a secret is empty.

**Redis.** Default user off. `publisher` may only `XADD` and `PING` on `wolfbet:*`; `dashboard` may only read and consume those streams. No RDB or AOF: the stream is a lossy live feed, and history is rebuilt from SQLite, so persistence would only add disk IO next to the bots.

**Caddy.** Automatic TLS for `WOLFBET_HOST`, admin API off, `Server` header removed, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP and a CSP with `frame-ancestors 'none'`. `/api/*` goes to `api` with the prefix stripped, `/ws*` goes to `api` unchanged, everything else is the SPA with an `index.html` fallback. A second listener on port 2020 (not published) answers the container healthcheck, because the TLS site cannot be checked from inside the container without the real hostname.

**CSP split.** The SPA boots from one inline script. SvelteKit emits a `<meta>` CSP with that script's SHA-256 (`mode: 'hash'`); Caddy's header allows `script-src 'self' 'unsafe-inline'`. Browsers enforce both policies, so the effective rule is "same origin plus that one hash". Styles allow `'unsafe-inline'` because SvelteKit's route announcer sets a `style` attribute, which hashes cannot cover; this permits CSS injection only, not script.

## Deferred

- Image build and vulnerability scans in CI.
- Database schema and migrations, settings module.
- The real ingest worker (the container runs a heartbeat-only placeholder so its healthcheck is real).
- Backups and restore drill.
- A pinned image digest instead of a version tag.
