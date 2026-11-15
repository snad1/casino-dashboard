# deploy

Host deployment for one service (WolfBet first). The compose stack runs `timescale`, `redis`, `api`, `ingest` and `caddy` on an internal network; only Caddy publishes ports. Resource caps keep the bots ahead of the dashboard for CPU and IO.

Files arrive with the deployment work: `compose.yml`, `Caddyfile`, `redis/users.acl`, `env.example`, and the install and backup scripts.
