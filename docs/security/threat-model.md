# Threat model

STRIDE per trust boundary. Each row names the threat, the control, and whether that control exists today or is planned. Planned controls are built by the story that adds the boundary, together with a negative test. Update this file when a boundary or control changes.

Assets: the casino `api_key` and bot config on the host, control over the bots (start, stop, config, terminal), bet history, user accounts and sessions, and the service secrets in `deploy/secrets/`.

## Browser to Caddy to api

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Spoofing | Stolen or forged session | Random session id in a `__Host-` httpOnly Secure SameSite=Strict cookie, only its sha256 stored; owner and admin view-only until TOTP is on; a fresh code for risky actions | Done |
| Tampering | Cross-site state change | CSRF token on every state-changing request; CORS off; same-origin SPA | Planned (auth) |
| Tampering | Injected script in the SPA | CSP without inline script in Caddy and the api; Svelte escaping, no `{@html}` on API data | Done |
| Repudiation | A control action nobody can trace | Every state-changing request writes a hash-chained `audit_log` row (actor, roles, action, redacted body, result, IP, request id); `X-Request-ID` on every response and log line; `python -m app.cli verify-audit` checks the chain | Done for HTTP; control, terminal, WebSocket and agent layers call the same recorder when they land |
| Tampering | History rewritten after an incident | The api runs as `dashboard_app` with only INSERT and SELECT on `audit_log`; a trigger refuses UPDATE, DELETE and TRUNCATE for every role; only the one-shot `migrate` container holds the owner password | Done |
| Information disclosure | Errors leak internals | Generic 500 and 422 bodies outside local/development/testing; full exception only in logs | Done |
| Information disclosure | Secrets in logs | Redaction of secret, token, password, api_key, cookie and authorization keys at any depth; paths logged without query strings; uvicorn access log off | Done |
| Information disclosure | Traffic sniffing or downgrade | TLS by Caddy, HSTS one year, `-Server` header | Done |
| Denial of service | Request floods | Container CPU and memory caps; rate limits on auth routes | Done |
| Elevation of privilege | Viewer reaches owner actions | Role checks as FastAPI dependencies; authz matrix test of every endpoint by every role | Planned (auth) |

## WebSocket

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Spoofing | Cross-site WebSocket hijack | Handshake refused before upgrade unless the session cookie is live, `Origin` is in `allowed_origins` and a 30 s one-time ticket from `POST /api/ws-ticket` matches that session; the session is re-checked every 60 s and the socket closes with 4401 when it ends; every connect and refusal is audited | Done |
| Denial of service | Connection or message floods | 5 sockets per user (6th refused with 429); frames over 16 KiB close the socket (uvicorn `--ws-max-size` and the app); uvicorn pings every 20 s and drops silent peers | Done |

## api to host agent

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Spoofing | Another local process talks to the agent | Unix socket mode 0660 and group, `SO_PEERCRED` uid check, shared `agent_token` | Planned (agent) |
| Tampering | Malformed or oversized requests | Versioned JSON-lines protocol, strict models, size and time limits, command allowlist | Planned (agent) |
| Information disclosure | The casino `api_key` reaches the browser | Agent responses mask it | Planned (agent) |

## Agent to host

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Elevation of privilege | Command injection | `create_subprocess_exec` with argv only, fixed binary paths, scrubbed environment, no unvalidated user string in argv | Planned (agent) |

## Bot daemon to Redis

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Spoofing | Unauthenticated client | Default user off; `publisher` and `dashboard` users with passwords from secret files | Done |
| Tampering | Publisher rewrites or deletes data | `publisher` may only `XADD` and `PING` on `wolfbet:*`; `dashboard` cannot write streams, only sign-in rate-limit sets on `auth:*` | Done |
| Information disclosure | Redis reachable from outside | Bound to 127.0.0.1 and the docker bridge only | Done |
| Denial of service | Memory exhaustion | `maxmemory 256mb`, container cap; live events are lossy by design | Done |

## Ingest to bot SQLite

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Tampering | Ingest writes to the bot database | Read-only bind mount of the bot directory; `mode=ro` URI | Mount done; reader planned |
| Denial of service | Long reads block the bot | Short read transactions (under 200 ms), batched reads with sleeps | Planned (history ingest) |

## api to Brevo, Telegram and Web Push

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Information disclosure | Provider keys leak | Keys only from files in `/run/secrets`, never from the environment; placeholder and weak values refused at startup; redacted in logs | Done |
| Spoofing | Lookalike provider endpoint | Fixed HTTPS endpoints with certificate verification; SMTP only over STARTTLS with a verified certificate | Done (email); planned (alerts) |
| Information disclosure | The internet-facing api leaks data outward or is used for SSRF | The api has no route out; only `mailer` joins `egress` and holds the mail key; mail bodies (one-time links) are cleared once sent or dead | Done |

## Service secrets

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| Information disclosure | Secrets in git, images or `docker inspect` | Files in a git-ignored, root-only `deploy/secrets/`; mounted as compose secrets; never env literals; gitleaks in `make check` and the commit hook | Done |
| Spoofing | Default or weak credentials | Services refuse to start on a missing, short, placeholder or low-variety secret | Done (api, Redis) |
