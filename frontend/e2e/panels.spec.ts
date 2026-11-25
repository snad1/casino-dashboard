import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { stubApi, viewerMe } from './stub';

const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: {
		1: {
			status: 'running',
			session_id: 7,
			profit: 0.5,
			total_bets: 10,
			bps_1s: 3,
			current_streak: 3,
			currency: 'btc',
			base_bet: 0.0001,
			current_bet: 0.0004,
			lane_split: 'even',
			run_profit: 0.25
		},
		2: { status: 'running', session_id: 8, profit: -0.1, total_bets: 4, current_streak: -2 },
		3: {
			status: 'stopped',
			session_id: 9,
			profit: 0,
			total_bets: 1,
			stop_reason: 'Max profit reached'
		}
	},
	tape: []
};

const system = (dropped: number, agent = 'ok') => ({
	op: 'system',
	topic: 'svc:wolfbet:system',
	ts_ms: 1,
	agent,
	host:
		agent === 'ok'
			? {
					cpu_pct: 12.5,
					load: [0.5, 0.4, 0.3],
					mem_total: 4 * 1024 ** 3,
					mem_available: 1024 ** 3,
					disk_total: 40 * 1024 ** 3,
					disk_free: 10 * 1024 ** 3,
					net_rx_bps: null,
					net_tx_bps: null
				}
			: null,
	units: [{ unit: 'wolfbet', cpu_pct: 4.2, rss_bytes: 50 * 1024 ** 2 }],
	containers: [],
	publisher: {
		1: { enabled: true, connected: true, sent: 100, queued: 0, dropped: 0 },
		2: { enabled: true, connected: true, sent: 90, queued: 0, dropped }
	}
});

const lastRun = {
	finished_at: '2026-01-05T10:00:00Z',
	kind: 'quick',
	mismatched: 0,
	persisting: 0,
	purged_upstream: 0,
	lag_rows: 5,
	lag_seconds: 1800,
	lag_alert: true
};

async function rig(page: Page): Promise<() => WebSocketRoute> {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: lastRun } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } })
	});
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op === 'subscribe' && message.topic === 'svc:wolfbet') {
				ws.send(JSON.stringify(SNAPSHOT));
			}
		});
	});
	return () => {
		if (!socket) throw new Error('socket was never opened');
		return socket;
	};
}

test('each lane gets a card with its state, bet sizes and stop reason', async ({ page }) => {
	await rig(page);
	await page.goto('/wolfbet');
	const cards = page.getByRole('article');
	await expect(cards).toHaveCount(3);
	const first = page.getByRole('article', { name: 'Lane 1' });
	await expect(first).toContainText('Running');
	await expect(first).toContainText('+0.50');
	await expect(first).toContainText('W3');
	await expect(first).toContainText('Base bet 0.0001');
	await expect(first).toContainText('Current bet 0.0004');
	await expect(first).toContainText('Split even');
	await expect(first).toContainText('Run profit +0.25');
	await expect(page.getByRole('article', { name: 'Lane 2' })).toContainText('L2');
	const third = page.getByRole('article', { name: 'Lane 3' });
	await expect(third).toContainText('Stopped');
	await expect(third).toContainText('Stopped: Max profit reached');
});

test('the system panel shows host, units, drops and history lag', async ({ page }) => {
	const socket = await rig(page);
	await page.goto('/wolfbet');
	await expect(page.getByRole('article', { name: 'Lane 1' })).toBeVisible();
	socket().send(JSON.stringify(system(0)));
	const host = page.getByRole('region', { name: 'Host' });
	await expect(host).toContainText('12.5%');
	await expect(host).toContainText('3.0 GB of 4.0 GB');
	await expect(page.getByRole('region', { name: 'Processes' })).toContainText('wolfbet');
	const alerts = page.getByRole('alert');
	await expect(alerts).toContainText(
		"History copy is 30 min behind, close to the bot's purge window."
	);

	socket().send(JSON.stringify(system(3)));
	await expect(alerts).toContainText('Lane 2 publisher dropped 3 events in the last minute.');

	socket().send(JSON.stringify(system(3, 'unavailable')));
	await expect(host).toContainText('Host metrics unavailable');
	await expect(page.getByRole('region', { name: 'Events and history' })).toContainText('Connected');
});

test('on a laptop the chart leads and session rows never run into each other', async ({ page }) => {
	await page.setViewportSize({ width: 1200, height: 800 });
	await rig(page);
	const long = (id: number) => ({
		epoch_id: 1,
		id,
		lane: 14,
		started_at: '2026-01-05T09:00:00Z',
		ended_at: '2026-01-05T09:00:30Z',
		currency: 'usdt',
		game: 'limbo-auto-with-a-very-long-game-name',
		strategy: 'Martingale with a long strategy name too',
		total_bets: 4,
		wins: 2,
		losses: 2,
		profit: 0.00000933,
		wagered: 0.0001,
		start_balance: 10,
		end_balance: 10.00000933
	});
	await page.route('**/api/wolfbet/sessions?**', (route) =>
		route.fulfill({
			status: 200,
			json: { items: [long(30), long(29), long(28)], next_cursor: null }
		})
	);
	await page.goto('/wolfbet');
	const sessions = page.getByRole('region', { name: 'Sessions' });
	const rows = sessions.getByRole('row');
	await expect(rows).toHaveCount(5);

	const boxes = await rows.evaluateAll((els) =>
		els.map((row) => {
			const own = row.getBoundingClientRect();
			const inner = [...row.querySelectorAll('a, span')].map((el) => el.getBoundingClientRect());
			return {
				top: own.top,
				bottom: own.bottom,
				inside: inner.every((b) => b.top >= own.top - 1 && b.bottom <= own.bottom + 1)
			};
		})
	);
	expect(boxes.every((b) => b.inside)).toBe(true);
	boxes.slice(1).forEach((box, i) => {
		expect(box.top).toBeGreaterThanOrEqual((boxes[i]?.bottom ?? 0) - 1);
	});

	const box = async (id: string) => {
		const found = await page.locator(`section#${id}`).boundingBox();
		if (!found) throw new Error(`${id} is not on the page`);
		return found;
	};
	const [chart, list, tape] = [await box('chart'), await box('sessions'), await box('tape')];
	expect(chart.y + chart.height).toBeLessThanOrEqual(list.y);
	expect(Math.abs(list.y - tape.y)).toBeLessThan(2);
	expect(tape.x + tape.width).toBeLessThanOrEqual(1200);
});
