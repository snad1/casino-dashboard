# contracts

Versioned wire formats shared by the dashboard and the bots.

| Path | Contents |
|---|---|
| `schemas/` | JSON Schemas for daemon events (each event carries `v`) and the agent protocol (each request carries `proto`). |
| `fixtures/` | Golden examples of each message, used by tests on both sides. |

Rules:

- Daemon events travel as one Redis stream field, `data`, holding the compact JSON the schema describes (`schemas/wolfbet-events-v1.json`).
- `wolfbet.py <read command> --json` prints one document per `schemas/wolfbet-cli-<command>-v1.json` (`status`, `lanes-status`, `config-show`, `stats`, `analytics`, `last-bets`, `session-bets`); each carries `v`, and an empty or failed state is `{"v": 1, "error": {"code", "message"}}`.
- `wolfbet.py --config-schema` follows `schemas/wolfbet-cli-config-schema-v1.json`; `wolfbet.py --dry-run --json --config-set KEY=VALUE ...` follows `schemas/wolfbet-cli-config-dry-run-v1.json`.
- `wolfbet.py --export ... --json` and `--backtest ... --json` follow `schemas/wolfbet-cli-export-v1.json` and `schemas/wolfbet-cli-backtest-v1.json`; a long backtest writes `schemas/wolfbet-cli-progress-v1.json` lines to stderr.
- The api talks to the host agent in JSON lines per `schemas/agent-proto-v1.json`: signed requests (`mac`, `ts`, one-time `id`) from an allowlisted uid/gid, generic error codes back.
- The Telegram bot publishes per-user streams `wolfbet-tg:<uid>:bets|state|lifecycle` per `schemas/wolfbet-tg-events-v1.json`: the daemon envelope with `svc` `wolfbet-tg` and `uid` in every payload. `fixtures/wolfbet_tg/` holds one golden event of each type; the bot copies them with their SHA-256.
- The agent talks to the Telegram bot's control socket in the same protocol (`wolfbet_tg.*` commands, the bot's own token). `fixtures/agent_proto_v1/` pins the wire bytes, a signed request and its replies; the bot copies them with their SHA-256.
- The Telegram bot's `/web` link signs a Telegram user into the dashboard with a token `base64url(payload).base64url(HMAC-SHA256)` over compact JSON `{exp, id, uid, v}`, keyed by the dashboard's `tg_login_secret`. `fixtures/tg_login_v1/` holds one golden token; the bot copies it with its SHA-256.
- A schema change bumps its version; consumers accept the versions they know and reject the rest.
- The bot repos copy a fixture into their own tests together with its SHA-256, so a change on either side without the other fails a test.
