# deploy

Docker Compose stack for one service's dashboard (WolfBet first), run on the same host as the bot.

| Service | Image | CPUs | Memory | Network | Published |
|---|---|---|---|---|---|
| `timescale` | `timescale/timescaledb:2.32.0-pg16` | 1.0 | 1g | internal | none |
| `redis` | `redis:7.4.12-alpine` | 0.25 | 320m | internal, hostlink | `127.0.0.1:6379` (for the bot daemon) |
| `api` | built from `../backend` | 1.0 | 512m | internal | none |
| `ingest` | built from `../backend` | 0.5 | 256m | internal | none |
| `mailer` | built from `../backend` | 0.25 | 128m | internal, egress | none |
| `migrate` | built from `../backend` (one-shot, before the app services) | 0.5 | 256m | internal | none |
| `caddy` | built from `caddy/Dockerfile` (SPA inside) | 0.25 | 128m | internal, edge | none (Coolify's proxy routes to port 80) |

Every service runs with `cpu_shares: 256` (the bots keep the default 1024), `no-new-privileges`, a healthcheck and `restart: unless-stopped`. `api`, `ingest` and `mailer` run as uid 10001 on a read-only root filesystem with all capabilities dropped. The `internal` network has no route out; only `mailer` also joins `egress`, so it alone can reach the mail provider, and only it holds the mail key.

## Install

The stack runs as a **Coolify Docker Compose resource** on the bot's host: Coolify owns Docker and the proxy on ports 80 and 443, terminates TLS for `wolfbet.<domain>` and routes it to the `caddy` service on port 80. Nothing else is published, except Redis on `127.0.0.1:6379` for the bot daemon.

1. Check out this repository on the server as `~/casino-dashboard` (`wolfctl dash` looks there; `WOLFCTL_DASH_DIR` overrides it), then prepare the host as root:

   ```bash
   cd ~/casino-dashboard
   sudo -E deploy/install.sh --host wolfbet.example.com --mail-from no-reply@example.com \
       --coolify-url https://coolify.example.com --coolify-app <resource uuid> --coolify-token-prompt
   ```

   It installs what is missing (`ufw`, `openssl`, `uv`), then the host agent (`agent/install.sh`, with the api container's uid allowed). It writes the secrets into `deploy/secrets/` and opens only the SSH port, 80 and 443 in ufw. It stops if Postgres or Redis listen on a public address, and checks that the name resolves to this server. It also saves the config `wolfctl dash` reads in `~/.config/casino-dashboard/` (mode 600) and prints the environment for Coolify. It never prints a secret and never replaces one that exists, so it is safe to run again. `--dry-run` shows the steps without changing anything. The Coolify flags are optional on a first run: add them later by running it again.

   Secrets it makes once with `openssl` (one value per file): `session_secret totp_key postgres_password postgres_app_password redis_publisher_password redis_dashboard_password`, the agent token copied from `~/.config/casino-agent/token`, and a push key pair (`vapid_private_key`; its public half goes in the printed environment). The Brevo key is asked for without echo, or taken from `--brevo-key-file`. `telegram_bot_token.txt` starts empty, which keeps Telegram alerts off. Telegram alerts use a bot of their own (from @BotFather), never the wolfbet TG bot; put its token in the file and redeploy.

   The directory (mode 700, root only) is what keeps the files private on the host. The files themselves are world-readable because compose bind-mounts them unchanged and three different container users read them: Redis (uid 999), the api (uid 10001) and Postgres. A missing file stops the deploy; a missing, short or placeholder secret makes the api exit, naming the secret but never printing it.

   Docker publishes container ports past ufw. Keep Coolify's own dashboard (port 8000) behind its domain or block it at the provider's firewall.

2. In Coolify, add a **Docker Compose** resource from this repository with the compose file `deploy/compose.yml`. Set the domain `https://wolfbet.example.com` on the `caddy` service (port 80), paste the environment the installer printed, and deploy. Coolify builds the images (the SPA is built inside the `caddy` image, so the host needs no Node). Every deploy runs `migrate` first; `api`, `ingest` and `mailer` start only after it succeeds, so a failing migration stops the deploy instead of crash-looping the api. `migrate` is the only container that gets the owner password (`postgres_password`); the api and mailer sign in as `dashboard_app`, whose password (`postgres_app_password`) the migration sets.

3. Create the owner (once; asks for the password twice):

   ```bash
   sudo -E deploy/install.sh --owner-email you@example.com
   ```

4. Check: `wolfctl dash status` shows the agent running, every container `(healthy)` and `Health: ok`.

### Every day

```bash
wolfctl dash status
wolfctl dash logs api            # or caddy, ingest, mailer, migrate, redis, timescale, agent
wolfctl dash update              # pull ~/casino-dashboard, update the agent, deploy in Coolify
wolfctl dash stop                # stack and agent only; the bots keep running
wolfctl dash start
```

To send mail through an SMTP relay instead, set `MAIL_BACKEND: smtp` plus `SMTP_HOST`, `SMTP_PORT` and `SMTP_USER` on `mailer`, and give it an `smtp_password` secret in place of `brevo_api_key`. The relay must offer STARTTLS; the mailer refuses to send without it.

Changing the push key pair later turns push off on every device until each user turns it on again.

## Coolify (system of record, wolfbet)

The live wolfbet dashboard is the Coolify app `hrrxmud5u4rwmjyilbqwryib` (project **Wolfbet**, environment **production**) on https://coolify.snadstudio.com, deployed from `deploy/compose.coolify.yml`. That file is the only description of the Coolify stack; `compose.yml` is the host fallback below.

- **Images:** `ghcr.io/snad1/casino-dashboard-api` (from `backend/`), `ghcr.io/snad1/casino-dashboard-caddy` (from `frontend/` with `deploy/caddy/Dockerfile`, the Caddyfile baked in) and `ghcr.io/snad1/casino-dashboard-redis` (`deploy/redis/Dockerfile`, the ACL template and entrypoint baked in), tagged with the commit sha and `latest`. Coolify copies no file besides the compose file, so nothing it runs bind-mounts a repo file. The packages are public, so Coolify pulls them without credentials. The Coolify env `IMAGE_TAG` picks the tag; it stays `latest` except while a rollback pins a sha.
- **Deploying:** a push to `feat/casino-dashboard` sends GitHub's push webhook to Coolify (`/webhooks/source/github/events/manual`, signed with the app's manual webhook secret), and Coolify deploys at once, pulling `:latest`. In parallel, `.github/workflows/images.yml` waits for that commit's `ci` run, stops unless it passed, then builds and pushes the three images. A push therefore deploys the newest images that finished building, which is normally the previous commit's: to run a commit's own images, press **Redeploy** in Coolify once its `images` run is green. `migrate` runs once per deploy; if it fails, api, ingest and mailer never start and Coolify shows the app unhealthy.
- **Credentials:** images are pushed with the run's own `GITHUB_TOKEN`; GitHub holds no Coolify token and no registry token. The webhook secret lives in Coolify (app, **Webhooks**) and in the repository's webhook settings.
- **Secrets:** one file each in `/opt/wolfbet/secrets` on the host (directory 0755, files 0644, because Redis runs as uid 999 and the api as uid 10001 and neither can enter `/root`). The compose file mounts them from those absolute paths. Never commit them.
- **Data:** Postgres lives in the Docker volume `hrrxmud5u4rwmjyilbqwryib_pgdata`. The volume `wolfbet-dashboard_pgdata` is the pre-Coolify copy, kept as a backup.
- **Status, logs, env, history:** Coolify's app page, or the API: `GET /api/v1/applications/<uuid>` (status), `GET /api/v1/applications/<uuid>/envs`, `GET /api/v1/deployments/applications/<uuid>` (history) and `GET /api/v1/deployments/<deployment uuid>` (log, including the `migrate` output).
- **Rolling back a release:** set `IMAGE_TAG` in Coolify to the last good commit's full sha and press **Redeploy**. Set it back to `latest` once the fix is pushed and built.

### Host fallback

`/root/wolfbet-deploy.sh` (cron every 5 minutes) builds and runs `compose.yml` on the host as the project `wolfbet-dashboard`. It does nothing while `/opt/wolfbet/DEPLOYER` says `coolify`, so the two deployers never fight. To fall back when Coolify cannot run the stack:

```bash
curl -fsS -H "Authorization: Bearer $(cat /root/.coolify_token)" \
  http://127.0.0.1:8100/api/v1/applications/hrrxmud5u4rwmjyilbqwryib/stop
cd /root/casino-dashboard
docker compose -f deploy/compose.yml --env-file /root/.wolfbet.env --project-name wolfbet-dashboard up -d --build
rm /opt/wolfbet/DEPLOYER    # the cron keeps the host stack up to date from now on
```

The host stack reads the volume `wolfbet-dashboard_pgdata`. Copy `hrrxmud5u4rwmjyilbqwryib_pgdata` into it first (both stacks stopped) if Coolify has run since the cutover; otherwise the data written under Coolify is not there.

## Redis users

| User | May do | Used by |
|---|---|---|
| `default` | nothing (off) | |
| `publisher` | `XADD`, `PING` on `wolfbet:*` and `wolfbet-tg:*` | the event publishers of the bot daemon and the Telegram bot |
| `dashboard` | read and consume streams on `wolfbet:*` and `wolfbet-tg:*` | `api` |

The ACL file is rendered at start from `redis/users.acl.tmpl` with the SHA-256 of each password, so no password text is stored in the repo, the environment or the process list. Redis keeps no data on disk: live events are disposable, and history comes from the bot's SQLite through `ingest`.
