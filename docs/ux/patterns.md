# UX patterns

Every screen of the dashboard follows the patterns below. Each one names where it was observed, how this dashboard applies it, and the accessibility rule it must meet. The patterns are described in our own words from public documentation; no branding, layout copy, screenshot or asset from any product is reused.

Sources were checked on 2026-11-19. Where a page could not be opened directly, the section says so.

Wireframes: [`wireframes/`](wireframes/) (dashboard, control, config, terminal, analytics, alerts, mobile). Tokens: [`frontend/src/lib/theme/tokens.css`](../../frontend/src/lib/theme/tokens.css).

## Sources

| Source | What we took from it |
|---|---|
| Binance Spot layout, help centre: [How to Spot Trade on Binance Website](https://www.binance.com/en/support/faq/115003765031), [Your Guide to Binance Spot Trading](https://www.binance.com/en/academy/articles/your-guide-to-binance-spot-trading) | Numbered regions: 24 h stats bar on top, chart in the centre, book and trades beside it, orders/history panel at the bottom |
| Binance Futures, help centre: [How to Customize the Binance Futures Trading Interface](https://www.binance.com/en/support/faq/a784518335b0492a9ebfa4a72e1ca092) | Modules can be added, removed, reordered and resized; a user choice of up/down colours; a "back to default layout" reset |
| Bybit, help centre: [How to Close Your Perpetual & Futures Position](https://www.bybit.com/en/help-center/article/How-to-close-Position) (direct fetch timed out; content from the search index summary) | "Close all" shows a confirmation window by default and warns about market price and slippage; the confirmation can be turned off in preferences |
| TradingView Lightweight Charts: [docs](https://tradingview.github.io/lightweight-charts/docs) (the chart library this dashboard ships) | Line, area and baseline series; `update()` for live points instead of `setData()`; one chart per container; theming through layout options |
| FreqUI, freqtrade's open-source web UI (GPL-3.0): [docs](https://www.freqtrade.io/en/stable/freq-ui/), [repo](https://github.com/freqtrade/frequi) | Dashboard of bot status and performance; trade view where the bot is started and stopped; settings for timezone and up/down candle colours; light and dark themes |
| Hummingbot Dashboard (Apache-2.0): [overview](https://hummingbot.org/dashboard/), [kill switch](https://hummingbot.org/client/global-configs/kill-switch/) | Separate pages for credentials, config, backtest, deploy and manage instances; a kill switch that stops a bot at a set performance threshold |
| W3C: [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | 1.4.1 use of colour, 1.4.3 and 1.4.11 contrast, 2.3.3 animation from interactions, 2.5.8 target size |

## 1. Layout grid

**Observed:** exchange trading pages put a stats bar on top and the chart in the centre, with live lists beside it and history below; regions can be resized or hidden (Binance Spot and Futures). Bot UIs split overview and trading into separate pages (FreqUI, Hummingbot).

**We apply:**

| Width | Layout |
|---|---|
| ≥ 1280 px | Ticker header across the top; nav rail; three panes: sessions (left, 260 px), chart (centre) with the lane cards row under it, bet tape (right, 284 px); system panel along the bottom |
| 768 to 1279 px | Two panes: sessions collapse into a drawer; the tape moves under the chart |
| < 768 px | One column with a bottom tab bar (see section 9) |

Panels keep fixed positions; there is no drag-and-drop layout in the first version. Gutter 16 px (`p-4`); radii from `--radius-md`. Wireframe: [`dashboard.svg`](wireframes/dashboard.svg).

## 2. Ticker header

**Observed:** a stats strip above the chart shows the pair, last price and 24 h change (Binance Spot).

**We apply:** a sticky 56 px header with service name, balance, session profit/loss, bps, link status and the age of the last event. The status is one of `live`, `stale`, `down` or `events off`, the same values the lane snapshot uses. Each status has a word and an icon, not only a colour. The global "Stop all" sits at the far right, apart from navigation (section 8).

**Accessibility:** status changes are announced through a polite live region, at most once per change.

## 3. Bet tape

**Observed:** a scrolling list of recent trades beside the chart, newest on top, with buy and sell in two colours (Binance Spot "market trades"; FreqUI trade list).

**We apply:**

- Fixed 28 px rows in a virtualised list holding at most 2 000 rows.
- Columns: time, lane, amount, multiplier, result, profit, balance.
- Numbers in `font-mono` with `tabular-nums`, so digits stay aligned while they change.
- Win or loss shown three ways: `text-profit` or `text-loss`, a ▲/▼ icon, and an explicit sign.
- Hovering or focusing the tape pauses insertion, and a "N new" chip resumes it.
- Lane filter in the tape header.

## 4. Profit and loss colour and delta animation

**Observed:** green for up and red for down, with the colour pair selectable in settings (Binance "colour preference", FreqUI candle colour setting).

**We apply:**

- Profit uses `--color-profit` and loss uses `--color-loss`. Every value also carries a sign and an arrow, because colour alone fails WCAG 1.4.1 and the green/red pair is the most common colour-vision confusion.
- When a value changes, its cell background flashes the matching colour at low opacity for `--motion-flash` (300 ms) with `--motion-ease`.
- Under `prefers-reduced-motion` the token becomes 0 ms, so there is no flash (WCAG 2.3.3); the arrow and sign still update.
- Swapping the up/down colours is a possible later setting. It only needs the two tokens redefined.

## 5. Chart controls

**Observed:** a toolbar over the chart for interval, chart type and settings; live data appended rather than redrawn (Binance Spot chart, Lightweight Charts docs).

**We apply:**

- A toolbar row: resolution `1s | 1m | 1h | 1d`, the same values the series API accepts. "Auto" lets the server choose within its point budget.
- Range presets: 1 h, 24 h, 7 d, 30 d, all.
- A profit/balance toggle, a lane filter and a crosshair readout of time and value.
- Live points are added with `update()`; history is loaded with `setData()` only on a range change.
- Session start and stop are shown as series markers.
- Chart colours come from the tokens, read at mount.

## 6. Settings drawer

**Observed:** settings open in a panel next to the content and come back to defaults with one reset (Binance Futures "Styles"); bot config lives on its own page (Hummingbot).

**We apply:** a right-side drawer, 400 px on desktop and full screen on mobile.

1. **Pick a field:** a dropdown for enums, or an input with its bounds shown.
2. **Save:** runs the server-side dry run.
3. **Confirm:** lists each change as `old → new` with any warnings.
4. **Apply:** shows the result as a toast.

"Back" returns to edit without losing input. Secrets are masked and never editable here. Focus moves into the drawer when it opens and back to the trigger when it closes. Wireframe: [`config.svg`](wireframes/config.svg).

## 7. Confirm modal wording

**Observed:** a closing action opens a confirmation window by default and states the consequence (market price, slippage) before it runs (Bybit).

**We apply** two levels:

| Level | Title | Body | Confirm | Button |
|---|---|---|---|---|
| Basic (pause, resume, reload, start) | Verb + target: "Pause lane 2?" | One line of effect: "Lane 2 stops placing bets until you resume it." | Click | Repeats the verb: "Pause lane 2" |
| Destructive (stop, restart, reset, lanes change) | Verb + target + service: "Reset statistics for wolfbet?" | What is lost and what is kept: "The current database is archived and a fresh one starts. Sessions stay in the archive." | Type the service name, plus a fresh second factor | The exact action: "Reset statistics" |

Sample copy:

- **Stop:** "Stop wolfbet? All lanes stop after their current bet. Open sessions are saved."
- **Restart:** "Restart wolfbet? Lanes stop and start again; the live view shows them as down for a few seconds."
- **Lanes:** "Change lanes from 3 to 2? Lane 3 stops and its session ends."

"Cancel" is always the left, secondary button. Enter never confirms a destructive action. Wireframe: [`control.svg`](wireframes/control.svg).

## 8. Pause and stop placement

**Observed:** start and stop sit with the bot's trade view (FreqUI); a kill switch stops a bot at a threshold (Hummingbot).

**We apply:**

- Per-lane Pause, Resume and Stop sit on each lane card.
- Service-wide actions live on the Control page.
- The one global "Stop all" sits in the header's far right, with a dashed danger outline. It is never next to "Save" or a nav link.
- A control the role cannot use stays visible but disabled, with the reason as text (for example "Needs operator role").
- Danger actions sit in a separate "Danger zone" block.

## 9. Mobile

**Observed:** exchanges ship a separate compact mobile layout rather than shrinking the desktop grid (Binance Futures layout modes).

**We apply:**

- Below 768 px: a compact ticker (profit/loss, status, bps) with "Stop all"; then the chart, the lane cards stacked one per row, and a collapsed tape showing the last 5 bets.
- A bottom tab bar: Live, Sessions, Control, Alerts.
- Touch targets are at least 44 × 44 px (`--touch-target`). That is above the WCAG 2.5.8 minimum of 24 px.
- Drawers and modals open full screen.

Wireframe: [`mobile.svg`](wireframes/mobile.svg).

## 10. Dark theme tokens

**Observed:** dark trading UIs with a light option, and user-chosen up/down colours (FreqUI themes, Binance colour preference).

**We apply:** `tokens.css` defines everything through Tailwind's `@theme static`, so it generates utilities (`bg-canvas`, `bg-surface`, `bg-raised`, `border-line`, `border-line-strong`, `text-ink`, `text-muted`, `text-accent`, `text-profit`, `text-loss`, `text-warn`, `text-info`). Components use only these utilities, never a literal colour.

- Dark is the default. Light follows `prefers-color-scheme` unless the page sets `data-theme`.
- Fonts are system stacks only, so no font download.
- Spacing has a 4 px base. Radii are 4, 8 and 12 px.
- Motion tokens: `--motion-flash` and `--motion-ease`.

The palette is our own and is not taken from any exchange.

### Contrast (WCAG 2.2: 4.5:1 for text, 3:1 for UI components)

| Pair | Dark | Light |
|---|---|---|
| `ink` on canvas / surface / raised | 15.39 / 14.18 / 12.81 | 16.90 / 17.96 / 15.86 |
| `muted` on canvas / surface / raised | 7.43 / 6.84 / 6.18 | 6.26 / 6.66 / 5.88 |
| `accent` on canvas / surface / raised | 6.88 / 6.34 / 5.73 | 6.18 / 6.57 / 5.80 |
| `profit` on canvas / surface / raised | 7.93 / 7.31 / 6.60 | 5.11 / 5.43 / 4.79 |
| `loss` on canvas / surface / raised | 5.97 / 5.50 / 4.97 | 5.78 / 6.15 / 5.43 |
| `warn` on canvas / surface / raised | 9.84 / 9.07 / 8.19 | 5.85 / 6.21 / 5.48 |
| `info` on canvas / surface / raised | 8.20 / 7.55 / 6.82 | 5.98 / 6.36 / 5.61 |
| `on-accent` on `accent` (button label) | 6.88 | 6.57 |
| `focus` on canvas / surface (focus ring) | 9.05 / 8.33 | 6.18 / 6.57 |
| `line-strong` on canvas / surface / raised (input borders) | 4.06 / 3.74 / 3.38 | 3.56 / 3.78 / 3.34 |

`line` is only for decorative dividers (1.34 and 1.43), never for the edge of a control.
