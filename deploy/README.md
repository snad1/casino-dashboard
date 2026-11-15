# deploy

Docker Compose stack for one service's dashboard (WolfBet first), run on the same host as the bot.

| Service | Image | CPUs | Memory | Network | Published |
|---|---|---|---|---|---|
| `timescale` | `timescale/timescaledb:2.32.0-pg16` | 1.0 | 1g | internal | none |
| `redis` | `redis:7.4.12-alpine` | 0.25 | 320m | internal, hostlink | `127.0.0.1:6379` (for the bot daemon) |
| `api` | built from `../backend` | 1.0 | 512m | internal | none |
| `ingest` | built from `../backend` | 0.5 | 256m | internal | none |
| `caddy` | `caddy:2.11.9-alpine` | 0.25 | 128m | internal, edge | `80`, `443` |

Every service runs with `cpu_shares: 256` (the bots keep the default 1024), `no-new-privileges`, a healthcheck and `restart: unless-stopped`. `api` and `ingest` run as uid 10001 on a read-only root filesystem with all capabilities dropped. The `internal` network has no route out.

## Install

Run from `casino-dashboard/` on the server, as the user that runs Docker.

1. Configuration:

   ```bash
   cp deploy/env.example deploy/.env      # set WOLFBET_HOST and BOT_DATA_DIR
   ```

2. Secrets, one value per file, never in `.env` or `compose.yml`:

   ```bash
   mkdir -p deploy/secrets && chmod 700 deploy/secrets
   for s in postgres_password redis_publisher_password redis_dashboard_password; do
     openssl rand -base64 36 | tr -d '\n' > deploy/secrets/$s.txt
     chmod 600 deploy/secrets/$s.txt
   done
   sudo chown 999 deploy/secrets/redis_*.txt   # Redis reads them as uid 999
   ```

   A missing file stops `docker compose up`; an empty Redis secret makes Redis refuse to start.

3. Build the SPA, then start:

   ```bash
   (cd frontend && pnpm install --frozen-lockfile && pnpm build)
   cd deploy && docker compose up -d --build
   docker compose ps          # all five show (healthy)
   ```

4. Check: `curl -s https://$WOLFBET_HOST/api/healthz` returns `{"status":"ok"}`.

After every `pnpm build`, run `docker compose restart caddy`: the build replaces `frontend/build`, and Caddy keeps serving the old directory until it remounts.

## Redis users

| User | May do | Used by |
|---|---|---|
| `default` | nothing (off) | |
| `publisher` | `XADD`, `PING` on `wolfbet:*` | the bot daemon's event publisher |
| `dashboard` | read and consume streams on `wolfbet:*` | `api` |

The ACL file is rendered at start from `redis/users.acl.tmpl` with the SHA-256 of each password, so no password text is stored in the repo, the environment or the process list. Redis keeps no data on disk: live events are disposable, and history comes from the bot's SQLite through `ingest`.
