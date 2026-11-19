import { mkdirSync, writeFileSync } from 'node:fs';
import type { Page, WebSocketRoute } from '@playwright/test';
import { adminMe, stubApi } from '../e2e/stub';

const LANES = [1, 2, 3];

/** A signed-in dashboard whose socket receives `ticksPerSecond` ticks, each with a bet per lane. */
export async function feed(page: Page, ticksPerSecond: number): Promise<() => void> {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } })
	});
	const timers: ReturnType<typeof setInterval>[] = [];
	await page.routeWebSocket(/\/ws\?ticket=/, (ws: WebSocketRoute) => {
		ws.onMessage((raw) => {
			if (JSON.parse(String(raw)).topic !== 'svc:wolfbet') return;
			const lanes = Object.fromEntries(
				LANES.map((n) => [n, { status: 'running', session_id: n, currency: 'btc', profit: 0 }])
			);
			ws.send(JSON.stringify({ op: 'snapshot', topic: 'svc:wolfbet', lanes, tape: [] }));
			let seq = 0;
			timers.push(
				setInterval(() => {
					seq += 1;
					const now = Date.now();
					const win = seq % 2 === 0;
					ws.send(
						JSON.stringify({
							op: 'tick',
							topic: 'svc:wolfbet',
							lanes: Object.fromEntries(
								LANES.map((n) => [n, { total_bets: seq, profit: seq * 1e-6, bps_1s: 10 }])
							),
							tape: LANES.map((n) => ({
								lane: n,
								seq,
								ts_ms: now,
								amount: 1e-5,
								multiplier: 2,
								result: win ? 70 : 20,
								state: win ? 'win' : 'loss',
								profit: win ? 1e-5 : -1e-5,
								balance: 0.01
							})),
							candles: LANES.map((n) => ({
								lane: n,
								t: Math.floor(now / 1000),
								profit: [0, seq * 1e-6, 0, seq * 1e-6],
								balance: [0.01, 0.01, 0.01, 0.01]
							}))
						})
					);
				}, 1000 / ticksPerSecond)
			);
		});
	});
	return () => timers.forEach(clearInterval);
}

export function save(name: string, data: unknown): void {
	mkdirSync('perf-results', { recursive: true });
	writeFileSync(`perf-results/${name}.json`, JSON.stringify(data, null, 1));
	console.log(name, JSON.stringify(data));
}

export const percentile = (values: number[], p: number): number => {
	const sorted = [...values].sort((a, b) => a - b);
	return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))] ?? 0;
};
