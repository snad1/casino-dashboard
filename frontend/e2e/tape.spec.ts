import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { stubApi, viewerMe } from './stub';

const LANES = {
	1: { status: 'running', session_id: 7, currency: 'btc', total_bets: 0, profit: 0 },
	2: { status: 'running', session_id: 4, currency: 'btc', total_bets: 0, profit: 0 }
};
const bet = (seq: number) => ({
	lane: seq % 2 === 0 ? 2 : 1,
	seq,
	ts_ms: Date.UTC(2026, 0, 5, 10) + seq * 20,
	amount: seq,
	multiplier: 2,
	result: 60,
	state: seq % 3 === 0 ? 'loss' : 'win',
	profit: seq % 3 === 0 ? -seq : seq,
	balance: 100
});
const session = (id: number, ended: boolean) => ({
	epoch_id: 1,
	id,
	lane: 1,
	started_at: '2026-01-05T09:00:00Z',
	ended_at: ended ? '2026-01-05T09:30:00Z' : null,
	currency: 'btc',
	strategy: 'flat',
	total_bets: 12,
	wins: 7,
	losses: 5,
	profit: 0.5,
	wagered: 12,
	start_balance: 100,
	end_balance: 100.5,
	max_win_streak: 3,
	max_loss_streak: 2
});

async function rig(page: Page, failSessions = false): Promise<() => WebSocketRoute> {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () =>
			failSessions
				? { status: 500, json: { detail: 'boom' } }
				: {
						status: 200,
						json: { items: [session(7, false), session(6, true)], next_cursor: null }
					},
		'GET /wolfbet/sessions/1/6': () => ({ status: 200, json: session(6, true) }),
		'GET /wolfbet/sessions/1/6/bets': () => ({
			status: 200,
			json: {
				items: [
					{
						id: 12,
						ts: '2026-01-05T09:29:00Z',
						amount: 1,
						multiplier: 2,
						rule: 'over',
						bet_value: 50,
						result_value: 61,
						state: 'win',
						profit: 1,
						balance_after: 100.5
					}
				],
				next_before: null
			}
		})
	});
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
		ws.onMessage(() =>
			ws.send(JSON.stringify({ op: 'snapshot', topic: 'svc:wolfbet', lanes: LANES, tape: [] }))
		);
	});
	return () => {
		if (!socket) throw new Error('the page never opened the live socket');
		return socket;
	};
}

/** Send `count` bets from `first` at 50 per second, five per 100 ms tick. */
async function replay(socket: WebSocketRoute, first: number, count: number) {
	for (let seq = first; seq < first + count; seq += 5) {
		const tape = Array.from({ length: 5 }, (_, i) => bet(seq + i));
		socket.send(JSON.stringify({ op: 'tick', topic: 'svc:wolfbet', lanes: {}, tape }));
		await new Promise((resolve) => setTimeout(resolve, 100));
	}
}

test('the tape shows the newest bet first and keeps only visible rows in the page', async ({
	page
}) => {
	const socket = await rig(page);
	await page.goto('/wolfbet');
	const tape = page.getByRole('table', { name: 'Bet tape' });
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
	await replay(socket(), 1, 100);
	await expect(tape).toHaveAttribute('aria-rowcount', '100');
	const firstRow = tape.getByRole('row').nth(1);
	await expect(firstRow).toContainText('100.00000000');
	await expect(firstRow).toContainText('Win');
	expect(await tape.getByRole('row').count()).toBeLessThan(45);
});

test('hovering pauses the tape and the new-rows button resumes it', async ({ page }) => {
	const socket = await rig(page);
	await page.goto('/wolfbet');
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
	await replay(socket(), 1, 10);
	const tape = page.getByRole('table', { name: 'Bet tape' });
	await tape.hover();
	await replay(socket(), 11, 10);
	await expect(page.getByRole('button', { name: '10 new' })).toBeVisible();
	await expect(tape.getByRole('row').nth(1)).toContainText('10.00000000');
	await page.getByRole('button', { name: '10 new' }).click();
	await expect(tape.getByRole('row').nth(1)).toContainText('20.00000000');
});

test('the lane filter shows one lane', async ({ page }) => {
	const socket = await rig(page);
	await page.goto('/wolfbet');
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
	await replay(socket(), 1, 20);
	await page.getByRole('combobox', { name: 'Lane' }).last().selectOption('2');
	const lanes = page.getByRole('table', { name: 'Bet tape' }).getByRole('cell').nth(1);
	await expect(lanes).toHaveText('2');
	const all = await page
		.getByRole('table', { name: 'Bet tape' })
		.getByRole('row')
		.evaluateAll((rows) => rows.slice(1).map((r) => r.children[1]?.textContent?.trim()));
	expect(new Set(all)).toEqual(new Set(['2']));
});

test('live sessions are pinned and a past session opens with its bets', async ({ page }) => {
	await rig(page);
	await page.goto('/wolfbet');
	const sessions = page.getByRole('table', { name: 'Sessions' });
	await expect(sessions.getByRole('row').first()).toContainText('#7 · Lane 1');
	await expect(sessions.getByRole('row').first()).toContainText('Live');
	await sessions.getByRole('link', { name: '#6 · Lane 1' }).click();
	await expect(page).toHaveURL('/wolfbet/sessions/1/6');
	await expect(page.getByRole('heading', { name: 'Session 6' })).toBeVisible();
	await expect(page.getByRole('table', { name: 'Bets' })).toContainText('Win');
});

test('a failed sessions load offers to try again', async ({ page }) => {
	await rig(page, true);
	await page.goto('/wolfbet');
	await expect(page.getByRole('alert')).toContainText("Couldn't load sessions. Try again.");
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
