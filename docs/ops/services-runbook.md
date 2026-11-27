# Runbook: put a service on the dashboard

How to bring goated, CryptoGames, DuckDice, Shuffle and Stake onto the dashboard next to WolfBet, on the VPS. Decision: [ADR 0003](../adr/0003-one-dashboard-many-services.md). Work: `specs/backlogs/2026-11-26-multi-service-dashboard.md`, whose "Runbook steps" table says which story unlocks each step.

Each step says what the dashboard or bot must already have. A step whose feature isn't there yet does nothing useful: skip it and come back. Never paste a secret into a command line, a chat or a file in a repo: every secret goes through a hidden prompt, stdin or a secrets file.

## Names per service

| Service | Repo dir | Install dir | ctl | Units | Vault service | Key kind |
|---|---|---|---|---|---|---|
| WolfBet | `~/wolfbet` | `~/wolfbet-bot` | `wolfctl` | `wolfbet`, `wolfbet-lane@K`, `wolfbet-tg` | `wolfbet` | `key` |
| goated | `~/goated` | `~/goated-bot` | `goatedctl` | `goated`, `goated-lane@K`, `goated-tg` | `goated` | `key` |
| CryptoGames | `~/cryptogames` | `~/cryptogames-bot` | `cgctl` | `cryptogames`, `cryptogames-lane@K`, `cryptogames-tg` | `cryptogames` | `key` |
| DuckDice | `~/duckdice` | `~/duckdice-bot` | `duckctl` | `duckdice`, `duckdice-lane@K`, `duckdice-tg` | `duckdice` | `key` |
| Shuffle | `~/shuffle` | `~/shuffle-bot` | `shufflectl` | `shuffle`, `shuffle-lane@K`, `shuffle-tg` | `shuffle` | `key` |
| Stake | `~/stakedotcom` | `~/stake-bot` | `stakectl` | `stake`, `stake-lane@K`, `stake-tg` | `stake` | `account` |

Below, `<svc>` is the dashboard name (`goated`, `cryptogames`, `duckdice`, `shuffle`, `stake`) and `<ctl>` its command.

## Steps

### 1. The bot is up to date (works now)

```sh
cd ~/<repo dir> && git status            # on main, clean
<ctl> update                             # rolls each lane at its session end
<ctl> status && <ctl> lanes
```

The Telegram bot is not restarted by `update`: `systemctl --user restart <svc>-tg`.

### 2. Keys are in the vault (works now)

```sh
<ctl> key add default                    # the main key, hidden prompt
<ctl> key add friend-a --label "Friend A"   # a second account, then: <ctl> config-set lane2.key_ref=friend-a
<ctl> key add telegram --kind telegram   # the Telegram bot token
<ctl> key list                           # labels and last 4 only
```

Stake keeps a login as one account of three secrets (access token, lockdown token, cookie header), each asked in turn:

```sh
stakectl key add main --kind account --label "Stake main login"
```

Once the dashboard has an adapter for the service, keys can be added on its Keys page instead; Stake's login needs the three-field account form.

### 3. Events reach Redis (needs per-service Redis users on the dashboard, and an event publisher in the bot; goated has one)

Each bot but WolfBet publishes to Redis as its own user, `<svc>-pub`, which may only append to `<svc>:*`. Make its password file once, without printing it; Redis reads it at its next start (step 5):

```sh
install -d -m 0755 /opt/wolfbet/secrets/publishers
[ -s /opt/wolfbet/secrets/publishers/<svc>.txt ] || \
  (umask 022; openssl rand -base64 36 | tr -d '\n' > /opt/wolfbet/secrets/publishers/<svc>.txt)
```

After step 5 has deployed, set the bot's events URL, again without printing the password:

```sh
<ctl> config-set events_enabled=true \
  "events_url=redis://<svc>-pub:$(python3 -c 'import urllib.parse; print(urllib.parse.quote(open("/opt/wolfbet/secrets/publishers/<svc>.txt").read().strip(), safe=""))')@127.0.0.1:9479"
<ctl> status                             # Events: on, connected yes
```

The password is percent-encoded: a base64 password can hold `/`, `+` or `=`, which would break the URL (the bot refuses it and saves nothing). `events_url` is a live setting: the running lanes pick it up without a restart. It is stored in the bot's config file (mode 0600). The password is on that one command line for a moment, visible to `ps` on the host; the VPS has no other users.

### 4. The agent drives the bot (needs a multi-service agent with a module for this bot)

The live agent is the system unit `casino-agent.service`. Find its env file and add the service:

```sh
systemctl cat casino-agent | grep EnvironmentFile
# in that file:
CASINO_AGENT_SERVICES=wolfbet,<svc>
CASINO_AGENT_<SVC>_INSTALL_DIR=/root/<install dir>
CASINO_AGENT_<SVC>_CTL=/root/.local/bin/<ctl>
CASINO_AGENT_<SVC>_REPO=/root/<repo dir>
systemctl restart casino-agent           # bots keep betting; only the agent restarts
```

Paths must be absolute; the agent refuses to start otherwise and names the variable.

### 5. The dashboard shows the service (needs the service registry, the switcher and an adapter for this bot)

In Coolify (app `hrrxmud5u4rwmjyilbqwryib`), add the service to the env and deploy:

```
DASHBOARD_SERVICES=wolfbet,<svc>
```

Then check that `gh run list` in casino-dashboard shows `ci` and `images` green; a red ci means nothing deployed. A redeploy restarts Redis, so each bot drops about a thousand live events; history is unaffected. Redis refuses to start when a listed service has no password file in `/opt/wolfbet/secrets/publishers/`, so make it first (step 3).

### 6. Updates from the dashboard (works once the dashboard serves the service)

- **Notice and button:** the service's Control page has an Updates card: `Update available: N commits (old to new)` with **Update**, which runs the rolling `<ctl> update` (each lane moves at its session end). It checks git at most every 2 minutes, and again after an update starts.
- **Auto-update on push (optional, off by default):**
  1. Make the webhook secret once, without printing it (one secret serves every bot repo):

     ```
     install -d -m 0755 /opt/wolfbet/secrets/hooks
     [ -s /opt/wolfbet/secrets/hooks/github_webhook_secret.txt ] || (umask 022; openssl rand -base64 36 | tr -d '\n' > /opt/wolfbet/secrets/hooks/github_webhook_secret.txt)
     ```

     Redeploy the dashboard so the api reads it; without the file the hook answers 404.
  2. On GitHub, in the bot repo's Settings > Webhooks, add `https://casino.snadstudio.com/api/hooks/github/<svc>`, content type `application/json`, event `Workflow runs` only, and the secret from that file (paste it into the GitHub form; it never goes in a repo).
  3. On the dashboard, open the service's Control page as the owner and tick **Update automatically after a green CI run on main** (it asks for a fresh code).

  Only a completed, successful push run on `main` starts it, once per commit; GitHub's Recent Deliveries shows 202 when an update started and 204 when nothing was due. Each start is in the audit log as `control.update` with trigger `github`.

### 7. Check it (after step 5)

1. Pick the service in the top bar: Live shows its lanes, and bets appear on the tape.
2. Control > Problems says `No problems.`
3. **Stop** lane 2, then **Start** lane 2: only lane 2 of that service stops, and its other lanes and WolfBet's keep betting. (goated has no pause, so this check uses Stop.)
4. Sessions and Analytics list the service's sessions; `<ctl> stats --json --lane 1` agrees.

### 8. Stake extras (works now; the dashboard's Cloudflare checks come later)

- FlareSolverr runs at its pinned version, bound to 127.0.0.1; `FLARESOLVERR_URL` in `~/stake-bot/.env` points at it.
- The Cloudflare Worker (`stakedotcom/cloudflare-worker/proxy.js`) requires its shared secret: `STAKE_PROXY_KEY` in `~/stake-bot/.env` and the Worker's secret must match; `STAKE_API_BASE` points at the Worker. Set both by editing the file (mode 0600), never on a command line.
- Each lane that bets from its own login keeps its own Cloudflare session.
- Stake's bet response carries no balance: the dashboard shows the bot's local running balance, labelled `tracked locally`.

## Progress per service

Tick a cell when the step is done on the VPS.

| Step | goated | CryptoGames | DuckDice | Shuffle | Stake |
|---|---|---|---|---|---|
| 1 Bot up to date | | | | | |
| 2 Keys in the vault | | | | | |
| 3 Events | | | | | |
| 4 Agent | | | | | |
| 5 Dashboard | | | | | |
| 6 Updates | | | | | |
| 7 Check | | | | | |
| 8 Stake extras | n/a | n/a | n/a | n/a | |
