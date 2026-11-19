import { expect, test, type WebSocketRoute } from '@playwright/test';
import { stubApi, viewerMe } from './stub';

const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: {
		1: {
			status: 'running',
			profit: 0.5,
			total_bets: 10,
			wins: 6,
			losses: 4,
			wagered: 20,
			balance: 100.12,
			currency: 'btc',
			bps_1s: 3,
			api_ms_p50: 40,
			api_ms_p99: 90
		},
		2: { status: 'events_off', profit: -0.1, total_bets: 5, wins: 2, losses: 3, wagered: 5 }
	},
	tape: []
};

async function liveRig(page: import('@playwright/test').Page) {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } })
	});
	const sockets: WebSocketRoute[] = [];
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		sockets.push(ws);
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op === 'subscribe') ws.send(JSON.stringify(SNAPSHOT));
		});
	});
	return (i: number): WebSocketRoute => {
		const found = sockets[i];
		if (!found) throw new Error(`socket ${i} was never opened`);
		return found;
	};
}

test('the header shows live totals and each lane state, and follows ticks', async ({ page }) => {
	const socket = await liveRig(page);
	await page.goto('/wolfbet');
	const header = page.getByRole('banner');
	await expect(header.getByRole('status')).toHaveText('Live');
	await expect(header).toContainText('+0.40000000');
	await expect(header).toContainText('Bets 15');
	await expect(header).toContainText('W/L 8/7');
	await expect(header).toContainText('Balance 100.12000000 btc');
	await expect(header).toContainText('ROI 1.60%');
	await expect(header).toContainText('API 40/90 ms');
	const lanes = header.getByRole('list', { name: 'Lanes' });
	await expect(lanes.getByRole('listitem')).toHaveText(['Lane 1: Running', 'Lane 2: Events off']);

	socket(0).send(
		JSON.stringify({ op: 'tick', topic: 'svc:wolfbet', lanes: { 1: { profit: 1.0 } }, tape: [] })
	);
	await expect(header).toContainText('+0.90000000');
});

test('a signed-out socket sends the viewer to sign-in', async ({ page }) => {
	const socket = await liveRig(page);
	await page.goto('/wolfbet');
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
	await socket(0).close({ code: 4401, reason: 'signed out' });
	await expect(page).toHaveURL('/login?next=%2Fwolfbet');
});
