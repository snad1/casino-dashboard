# Security review, 2026

This is the review that comes before the old web retires. It ran on 2026-11-21, on the throwaway e2e stack from `tools/e2e_stack.sh up`, at `feat/casino-dashboard` `3b8ecf8` plus the changes listed under "Fixed in this review". All tests ran with `APP_ENV=testing`. The backend tests use their own testcontainers database.

On the VPS, the header re-check at the end of this page passed on 2026-11-21. The real-data restore drill in `docs/ops/restore.md` is still to run.

## Result

| Item | Result | Evidence |
|---|---|---|
| Authz matrix | Pass | 137 passed: `test_permissions.py`, `test_control_api.py`, `test_route_auth.py`, `test_history_api.py`, `test_analytics_api.py`, `test_config_api.py`, `test_admin_users.py` |
| CSRF | Pass | `test_route_auth.py::test_a_signed_in_browser_changes_nothing_without_the_csrf_token`; `test_login.py` + `test_audit.py`, 37 passed |
| WebSocket origin | Pass | `test_ws_auth.py`, 13 passed, including `test_foreign_or_missing_origin_is_refused`; `test_terminal.py::test_a_foreign_origin_is_refused` |
| Agent fuzz | Pass | `agent/tests/test_fuzz_args.py`, 3 passed (hypothesis) |
| Terminal controls | Pass | `test_terminal.py`, 23 passed |
| Secrets | Pass | `test_settings.py` + `test_deploy_install.py` + `test_deploy_compose.py`, 93 passed; `make gitleaks` clean on the full history and the tree |
| Headers | Pass | `test_security_headers.py`, 3 passed; the stack's live headers, listed below |
| Backup restore | Pass locally | The drill restored every row and the timing is recorded in `docs/ops/restore.md`. The VPS drill is pending |
| ZAP baseline | Pass | `make zap`: 0 FAIL, 0 WARN, 63 rules passed; 4 accepted as INFO and 1 ignored, each with its reason in `deploy/zap/rules.tsv` |
| Scanners | Pass | `make security`: bandit, pip-audit, pnpm-audit, semgrep, gitleaks, hadolint and trivy all clean |

## Authz matrix

`test_route_auth.py` walks every route in the api's own OpenAPI schema with no session. Only six routes answer an anonymous caller:
- `GET /healthz`;
- `POST /auth/login`;
- `POST /auth/logout`;
- `POST /auth/invites/accept`;
- `POST /auth/password-reset`;
- `POST /auth/password-reset/confirm`.

Each of these checks its own token or password. Logout only ends the session the request carries. Every other route answers 401 or 403. A new route fails this test until it is protected or added to the list on purpose.

What each role may do is covered by the other files. `test_every_action_for_every_role` presses every control action as every role. `test_permissions.py` covers the role table and the 2FA gate.

## CSRF

A signed-in owner sends every state-changing route without `X-CSRF-Token`. Each one answers 403 with "Your session expired. Reload the page and try again." Login has no session yet, so it checks `Origin` instead (`test_login.py::test_requires_an_allowed_origin`).

## Headers

These are the stack's response headers on `https://localhost:8443/`:

```
content-security-policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
cross-origin-opener-policy: same-origin
cross-origin-resource-policy: same-origin
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
referrer-policy: no-referrer
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
```

The page also carries a meta policy, `script-src 'self' 'sha256-...'`. A browser enforces both policies, so only the hashed bootstrap script runs inline. Files under `/_app/immutable/` are sent with `Cache-Control: public, max-age=31536000, immutable`.

## ZAP baseline

Run it with `make zap`. It starts the e2e stack, runs ZAP 2.17.0 `zap-baseline.py` from inside the stack's edge network, writes `zap-results/report.html` and removes the stack.

ZAP's Java client sends no SNI for a bare `localhost`, so the scan reaches Caddy as `zap.localhost`. Any WARN or FAIL that `deploy/zap/rules.tsv` does not accept fails the target.

| Alert | Status | Why |
|---|---|---|
| 10055 CSP: style-src unsafe-inline | INFO | Svelte sets inline styles. Scripts are locked by the hashed meta policy |
| 90004 Cross-Origin-Embedder-Policy | INFO | No cross-origin isolation feature is used. Every response sends CORP same-origin |
| 10015 Re-examine Cache-control | INFO | The HTML shell revalidates by ETag. Hashed assets are immutable |
| 10049 Storable and Cacheable Content | INFO | The same: immutable assets, and a shell and worker that revalidate |
| 10109 Modern Web Application | IGNORE | Informational: this is a single-page app |

## Fixed in this review

- **CSP `connect-src`:** `'self' wss:` became `'self'`. Same-origin sockets are still allowed, and sockets to any other host are now refused. The stack's live spec on desktop still streams over the socket.
- **`Cross-Origin-Resource-Policy: same-origin`:** added to every response.
- **Hashed build files:** now sent as immutable for a year.
- **semgrep suppressions:** four existing lines in the agent are now flagged by the live registry rules. Each got a suppression with its reason:
  - three `os.chmod` calls: `0o700`, and `0o660` for the agent's group;
  - one log message that contains the word "credentials".
- **gitleaks:** the scan of the working tree flagged the throwaway e2e password inside Playwright's page snapshots. `frontend/test-results/`, `frontend/playwright-report/` and `zap-results/` are now in the allowlist as generated output.

## Opened as stories

| Story | Root cause |
|---|---|
| Pin the semgrep rules | `make semgrep` loads `p/python` and `p/owasp-top-ten` from the registry on every run. New registry rules failed this review with no code change, and they will fail CI the same way |
| Fix the stack e2e specs | Six specs already fail on `3b8ecf8`; they fail the same way with or without this review's changes. The two mobile live and session specs stop at "Too many sign-in attempts": the suite signs the one viewer in once per test and project from one IP, and the login limit stops it. Chart (history request timeout), comments (live update for a second viewer) and Lighthouse (no accessibility score on /login) still need their root cause found |

## VPS re-check (owner)

Once the stack is live at `wolfbet.<domain>`:

```bash
curl -sI https://wolfbet.<domain>/ | grep -iE 'content-security|strict-transport|x-content-type|referrer|cross-origin|permissions'
curl -s -o /dev/null -w '%{http_code}\n' https://wolfbet.<domain>/api/metrics     # 404
```

The headers must match the list above. Traefik may add headers of its own, but it must not remove any of these.

**Result, 2026-11-21:** PASS. Checked from outside the server against `https://wolfbet.snadstudio.com/`:
- the response is `HTTP/1.1 200`, and all seven headers match the list above;
- `/api/metrics` answers 404.

The site sits behind an nginx proxy that sends `Server: nginx/1.24.0 (Ubuntu)`. Caddy removes its own `Server` header, but nginx adds this one. `server_tokens off;` in nginx's `http {}` block hides the version.

The same `curl` run on the VPS itself returned `000`: the server could not reach its own public name. A check from the VPS needs `--resolve wolfbet.snadstudio.com:443:127.0.0.1`.
