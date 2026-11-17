# contracts

Versioned wire formats shared by the dashboard and the bots.

| Path | Contents |
|---|---|
| `schemas/` | JSON Schemas for daemon events (each event carries `v`) and the agent protocol (each request carries `proto`). |
| `fixtures/` | Golden examples of each message, used by tests on both sides. |

Rules:

- Daemon events travel as one Redis stream field, `data`, holding the compact JSON the schema describes (`schemas/wolfbet-events-v1.json`).
- A schema change bumps its version; consumers accept the versions they know and reject the rest.
- The bot repos copy a fixture into their own tests together with its SHA-256, so a change on either side without the other fails a test.
