# Performance, accessibility and end-to-end checks

| Check | Command | Where it runs | Fails when |
|---|---|---|---|
| Bundle budget | `make budget` | CI, every push | any page's first load is over 250 KB gzip |
| Stubbed browser suite, axe, keyboard | `make e2e` | CI, every push | a flow breaks, axe finds a serious or critical WCAG 2.2 AA issue, or focus order or visibility regresses |
| Stack suite and Lighthouse | `make e2e-stack` | CI on pull requests to main and nightly; needs Docker locally | a real-stack flow breaks, or Lighthouse accessibility < 0.95 or best practices < 0.9 |
| Frame rate | `make perf` | by hand | never; prints numbers to record below |
| Memory soak | `make soak` (`SOAK_MINUTES=60`) | by hand | the JS heap grows more than 20% between the first and last sixth of the run |

## Installable app

Lighthouse 13 has no PWA category; Lighthouse removed it in version 12. `e2e/pwa.spec.ts` asks Chromium itself through `Page.getInstallabilityErrors`, the same check the browser runs before it offers "Install". The same spec checks the manifest, the icons, the offline page, and that no `/api` response ever comes from the service worker or its cache.

## Bundle budget

A page's first load is the client entry, every layout above the page and the page itself, with their static imports and CSS. Chunks loaded with `import()` (the chart library, and the terminal later) are not counted, so heavy libraries must stay behind a dynamic import. `BUNDLE_BUDGET_KB` overrides the 250 KB limit for a trial run.

Measured 2026-11-19: the largest page is the dashboard at 67 KB gzip. The chart library is a separate 60 KB chunk loaded after the page.

## Stack suite

`make e2e-stack` runs `tools/e2e_stack.sh up`, which does four things:

1. Generates throwaway secrets in `deploy/e2e-secrets/`.
2. Builds the frontend and the api image.
3. Starts compose project `wolfbet-e2e` with `deploy/compose.e2e.yml` on `https://localhost:8443`.
4. Migrates, then seeds one viewer and six past sessions (`tools/e2e_seed.py`).

A `replay` service sends `tools/fixtures/wolfbet-events.jsonl` at 10x speed on a loop as the bot's `publisher` Redis user. Playwright then signs in on desktop and phone viewports and checks these flows:

- live ticker and lane cards;
- chart zoom loading older history;
- opening a past session;
- a comment seen live by a second viewer;
- reconnecting after `docker compose restart api`.

Lighthouse runs in the same signed-in browser. The stack and its volumes are removed afterwards. `KEEP_STACK=1 make e2e-stack` leaves the stack up for debugging, and `tools/e2e_stack.sh down` removes it.

To record a new fixture from a live bot, run `uv run --project backend python tools/replay_publisher.py --redis <dashboard-user-url> record --out file.jsonl --seconds 120`.

## Frame rate and memory

Both run against a production build with a simulated socket. Each tick carries every lane, one bet per lane and a candle per lane. `make perf` samples `requestAnimationFrame` intervals and long tasks for 30 s at 1x and at 4x CPU throttle. The 4x run stands in for a mid-range phone.

| Date | Machine | CPU | fps | p50 frame | p95 frame | Long tasks |
|---|---|---|---|---|---|---|
| 2026-11-19 | Apple silicon laptop, headless Chromium | 1x | 60.1 | 16.7 ms | 16.7 ms | 0 |
| 2026-11-19 | same, 4x throttle | 4x | 59.9 | 16.7 ms | 16.7 ms | 0 |

`make soak` feeds 100 ticks a second, ten times the live rate. It forces a garbage collection and reads the JS heap every minute. A 3-minute trial on 2026-11-19 went from 6.6 MB to 7.5 MB (+12%). That run is too short for the full check, so run the full hour before release and record it here.
