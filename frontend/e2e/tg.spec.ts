import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { adminMe, stubApi, tgMe, type Handler } from './stub';

const UID = 42;
const TOKEN = 'eyJleHAiOjEsImlkIjoiYSIsInVpZCI6NDIsInYiOjF9.signature-part-of-the-token';
const SESSIONS = [
	{
		epoch_id: 7,
		id: 2,
		lane: 1,
		started_at: '2026-01-05T12:00:00Z',
		ended_at: '2026-01-05T12:05:00Z',
		currency: 'btc',
		strategy: 'Martingale',
		total_bets: 3,
		wins: 2,
		losses: 1,
		profit: 1.0,
		wagered: 3,
		start_balance: 100,
		end_balance: 101,
		max_win_streak: 2,
		max_loss_streak: 1
	}
];
const DETAIL = {
	...SESSIONS[0],
	game: 'dice',
	base_bet: 1,
	multiplier: 2,
	highest_balance: 101,
	lowest_balance: 99,
	highest_win: 1,
	biggest_loss: 1,
	bets_per_minute: 1,
	bets_per_second: 0.1,
	peak_bps: 1,
	low_bps: 0,
	peak_bpm: 1,
	low_bpm: 0
};
const bet = (id: number) => ({
	id,
	ts: '2026-01-05T12:00:00Z',
	amount: 1,
	multiplier: 2,
	rule: 'over',
	bet_value: 50,
	result_value: 60,
	state: 'win',
	profit: 1,
	balance_after: 100 + id
});

function tgApi(extra: Record<string, Handler> = {}): Record<string, Handler> {
	const base = `/tg/${UID}`;
	return {
		'GET /auth/me': () => ({ status: 200, json: tgMe(UID) }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		[`GET ${base}/status`]: () => ({
			status: 200,
			json: { sessions: [{ slot: 1, session_id: 2, paused: false, profit: 0.5 }] }
		}),
		[`GET ${base}/series`]: () => ({ status: 200, json: { res: '1s', series: [] } }),
		[`GET ${base}/sessions`]: (_, url) => ({
			status: 200,
			json: url.searchParams.get('cursor')
				? { items: [{ ...SESSIONS[0], id: 1 }], next_cursor: null }
				: { items: SESSIONS, next_cursor: '7:2' }
		}),
		[`GET ${base}/sessions/7/2`]: () => ({ status: 200, json: DETAIL }),
		[`GET ${base}/sessions/7/2/bets`]: (_, url) => ({
			status: 200,
			json: url.searchParams.get('before')
				? { items: [bet(201)], next_before: null }
				: { items: [bet(203), bet(202)], next_before: 202 }
		}),
		[`GET ${base}/config`]: () => ({
			status: 200,
			json: { config: { currency: 'btc', max_loss: 0.5, custom_rules_text: '' } }
		}),
		...extra
	};
}

async function liveSocket(page: Page): Promise<() => WebSocketRoute> {
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op === 'subscribe' && message.topic === `svc:wolfbet-tg:user:${UID}`) {
				ws.send(
					JSON.stringify({
						op: 'snapshot',
						topic: message.topic,
						lanes: { 1: { status: 'running', profit: 0.5, total_bets: 3, currency: 'btc' } },
						tape: []
					})
				);
			}
		});
	});
	return () => {
		if (!socket) throw new Error('the socket was never opened');
		return socket;
	};
}

test('a link from the bot signs in once and lands on My bot with the token gone', async ({
	page,
	isMobile
}) => {
	let signedIn = false;
	const posted: unknown[] = [];
	await liveSocket(page);
	await stubApi(
		page,
		tgApi({
			'GET /auth/me': () =>
				signedIn ? { status: 200, json: tgMe(UID) } : { status: 401, json: { detail: 'x' } },
			'POST /auth/telegram': (body) => {
				posted.push(body);
				signedIn = true;
				return { status: 204 };
			}
		})
	);
	await page.goto(`/tg/login#token=${TOKEN}`);
	await expect(page.getByRole('heading', { name: 'My bot' })).toBeVisible();
	expect(page.url()).toMatch(new RegExp(`/tg/${UID}$`));
	expect(posted).toEqual([{ token: TOKEN }]);
	const nav = page.getByRole('navigation', { name: isMobile ? 'Tabs' : 'Main' });
	await expect(nav.getByRole('link')).toHaveText(['My bot', 'Sessions', 'Settings']);
});

test('a used or expired link says so and how to get a new one', async ({ page }) => {
	await stubApi(page, {
		'POST /auth/telegram': () => ({
			status: 401,
			json: { detail: 'This link is invalid or expired. Send /web to the bot again.' }
		})
	});
	await page.goto(`/tg/login#token=${TOKEN}`);
	await expect(
		page.getByText('This link is invalid or expired. Send /web to the bot again.')
	).toBeVisible();
	expect(page.url()).not.toContain('token=');
});

test('My bot shows the live lane and follows its ticks', async ({ page }) => {
	const socket = await liveSocket(page);
	await stubApi(page, tgApi());
	await page.goto(`/tg/${UID}`);
	const lane = page.getByRole('article', { name: 'Lane 1' });
	await expect(lane).toContainText('Running');
	await expect(lane).toContainText('+0.50000000');
	socket().send(
		JSON.stringify({
			op: 'tick',
			topic: `svc:wolfbet-tg:user:${UID}`,
			lanes: { 1: { profit: 0.75 } },
			tape: []
		})
	);
	await expect(lane).toContainText('+0.75000000');
});

test('Pause asks first, sends once and shows the bot refusing', async ({ page }) => {
	await liveSocket(page);
	const sent: Array<{ body: unknown }> = [];
	let refuse = false;
	await stubApi(
		page,
		tgApi({
			[`POST /tg/${UID}/control/pause`]: (body) => {
				sent.push({ body });
				return refuse
					? { status: 404, json: { detail: 'No session is running there.' } }
					: { status: 200, json: { slots: [1], session_id: null } };
			}
		})
	);
	await page.goto(`/tg/${UID}`);
	await page.getByRole('button', { name: 'Pause' }).click();
	const dialog = page.getByRole('dialog', { name: 'Pause this session?' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Pause' }).click();
	await expect(page.getByText('Pause: done.')).toBeVisible();
	expect(sent).toEqual([{ body: { slot: 1 } }]);

	refuse = true;
	await page.getByRole('button', { name: 'Pause' }).click();
	await page
		.getByRole('dialog', { name: 'Pause this session?' })
		.getByRole('button', { name: 'Pause' })
		.click();
	await expect(page.getByText('No session is running there.')).toBeVisible();
});

test('sessions page, a session with its bets, and the CSV links', async ({ page }) => {
	await liveSocket(page);
	await stubApi(page, tgApi());
	await page.goto(`/tg/${UID}/sessions`);
	await expect(page.getByRole('link', { name: 'Download CSV' })).toHaveAttribute(
		'href',
		`/api/tg/${UID}/sessions.csv`
	);
	await page.getByRole('link', { name: '#2 · Lane 1' }).click();
	await expect(page.getByRole('heading', { name: 'Session 2' })).toBeVisible();
	await expect(page.getByText('Martingale')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Download bets CSV' })).toHaveAttribute(
		'href',
		`/api/tg/${UID}/sessions/7/2/bets.csv`
	);
	await expect(page.getByRole('row').filter({ hasText: 'Win' })).toHaveCount(2);
});

test('settings show the saved values read-only', async ({ page }) => {
	await liveSocket(page);
	await stubApi(page, tgApi());
	await page.goto(`/tg/${UID}/settings`);
	await expect(page.getByText('Stop at loss')).toBeVisible();
	await expect(page.getByText('0.5', { exact: true })).toBeVisible();
	await expect(page.getByText('Not set')).toBeVisible();
	await expect(page.getByRole('textbox')).toHaveCount(0);
});

test('a Telegram account is kept on its own pages', async ({ page }) => {
	await liveSocket(page);
	await stubApi(page, tgApi());
	await page.goto('/');
	await expect(page).toHaveURL(new RegExp(`/tg/${UID}$`));
	await page.goto('/wolfbet');
	await expect(page).toHaveURL(new RegExp(`/tg/${UID}$`));
	await page.goto('/tg/43');
	await expect(
		page.getByRole('heading', { name: 'You do not have permission to do that.' })
	).toBeVisible();
});

test('admins list the Telegram users and open one', async ({ page }) => {
	await liveSocket(page);
	await stubApi(
		page,
		tgApi({
			'GET /auth/me': () => ({ status: 200, json: adminMe }),
			'GET /tg/users': () => ({
				status: 200,
				json: {
					items: [
						{ uid: UID, sessions: 2, bets: 6, profit: 1.5, last_session_at: '2026-01-05T12:00:00Z' }
					]
				}
			})
		})
	);
	await page.goto('/tg/users');
	await expect(page.getByRole('heading', { name: 'Telegram users' })).toBeVisible();
	await page.getByRole('link', { name: String(UID) }).click();
	await expect(page.getByRole('heading', { name: 'My bot' })).toBeVisible();
	await expect(page.getByRole('article', { name: 'Lane 1' })).toBeVisible();
});
