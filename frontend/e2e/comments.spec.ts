import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { adminMe, stubApi, viewerMe, type Reply } from './stub';

const XSS = '<img src=x onerror="window.__xss = 1">';
const SESSION = {
	epoch_id: 1,
	id: 6,
	lane: 1,
	started_at: '2026-01-05T09:00:00Z',
	ended_at: '2026-01-05T09:30:00Z',
	currency: 'btc',
	strategy: 'flat',
	total_bets: 1,
	wins: 1,
	losses: 0,
	profit: 0.5,
	wagered: 1,
	start_balance: 100,
	end_balance: 100.5,
	max_win_streak: 1,
	max_loss_streak: 0
};
const BET = {
	id: 12,
	ts: '2026-01-05T09:29:00Z',
	amount: 1,
	multiplier: 2,
	rule: 'over',
	result_value: 60,
	state: 'win',
	profit: 1,
	balance_after: 100.5
};

type Comment = Record<string, unknown> & { id: number; kind: string; body: string };

const comment = (id: number, body: string, mine: boolean, over: object = {}): Comment => ({
	id,
	kind: 'session',
	epoch_id: 1,
	session_id: 6,
	bet_id: null,
	at: null,
	body,
	author: mine ? 'viewer' : 'someone',
	created_at: '2026-01-05T10:00:00Z',
	edited_at: null,
	can_edit: mine,
	can_delete: mine,
	...over
});

interface Rig {
	comments: Comment[];
	calls: string[];
	socket: () => WebSocketRoute;
}

async function rig(
	page: Page,
	options: { me?: object; start?: Comment[]; post?: (body: Comment) => Reply } = {}
): Promise<Rig> {
	const comments = [...(options.start ?? [])];
	let next = 100;
	const calls = await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: options.me ?? viewerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } }),
		'GET /wolfbet/sessions/1/6': () => ({ status: 200, json: SESSION }),
		'GET /wolfbet/sessions/1/6/bets': () => ({
			status: 200,
			json: { items: [BET], next_before: null }
		}),
		'GET /wolfbet/series': (_body, url) => {
			const to = Date.parse(url.searchParams.get('to') ?? '');
			const t = (offset: number) => new Date(to - offset).toISOString();
			const points = Array.from({ length: 60 }, (_, i) => [t((60 - i) * 30_000), i / 10]);
			return { status: 200, json: { res: '1s', series: [{ lane: 1, points }] } };
		},
		'GET /wolfbet/comments': (_body, url) => {
			const kind = url.searchParams.get('kind');
			const bet = url.searchParams.get('bet_id');
			const items = comments.filter(
				(c) => c.kind === kind && (kind !== 'bet' || String(c.bet_id) === bet)
			);
			return { status: 200, json: { items } };
		},
		'POST /wolfbet/comments': (body) => {
			const sent = body as Comment;
			if (options.post) return options.post(sent);
			const made = comment(next++, sent.body, true, sent);
			comments.push(made);
			return { status: 201, json: made };
		},
		'DELETE /wolfbet/comments/1': () => {
			comments.splice(
				comments.findIndex((c) => c.id === 1),
				1
			);
			return { status: 204 };
		}
	});
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
	});
	return {
		comments,
		calls,
		socket: () => {
			if (!socket) throw new Error('socket was never opened');
			return socket;
		}
	};
}

const sessionBlock = (page: Page) => page.getByRole('region', { name: 'Session comments' });

test('a viewer posts on a session and the text shows as plain text', async ({ page }) => {
	let dialogs = 0;
	page.on('dialog', (dialog) => {
		dialogs += 1;
		void dialog.dismiss();
	});
	await rig(page);
	await page.goto('/wolfbet/sessions/1/6');
	const block = sessionBlock(page);
	await expect(block).toContainText('No comments yet.');
	await block.getByLabel('Add a comment').fill(XSS);
	await expect(block).toContainText(`${XSS.length} of 2000`);
	await block.getByRole('button', { name: 'Post' }).click();
	await expect(block.getByRole('listitem')).toContainText(XSS);
	await expect(block.getByRole('listitem')).toContainText('viewer');
	await expect(block.locator('img')).toHaveCount(0);
	expect(await page.evaluate(() => (window as { __xss?: number }).__xss)).toBeUndefined();
	expect(dialogs).toBe(0);
});

test('only your own comments offer Edit and Delete; a moderator may delete', async ({ page }) => {
	const start = [comment(1, 'mine', true), comment(2, 'theirs', false)];
	await rig(page, { start });
	await page.goto('/wolfbet/sessions/1/6');
	const items = sessionBlock(page).getByRole('listitem');
	await expect(items.nth(0).getByRole('button', { name: 'Edit' })).toBeVisible();
	await expect(items.nth(1).getByRole('button')).toHaveCount(0);

	const admin = await page.context().newPage();
	await rig(admin, { me: adminMe, start: [comment(1, 'theirs', false, { can_delete: true })] });
	await admin.goto('/wolfbet/sessions/1/6');
	const theirs = sessionBlock(admin).getByRole('listitem');
	await expect(theirs.getByRole('button', { name: 'Edit' })).toHaveCount(0);
	await theirs.getByRole('button', { name: 'Delete' }).click();
	await theirs.getByRole('button', { name: 'Delete comment' }).click();
	await expect(sessionBlock(admin)).toContainText('No comments yet.');
});

test('a bet opens its own comments', async ({ page }) => {
	await rig(page, { start: [comment(3, 'on the bet', false, { kind: 'bet', bet_id: 12 })] });
	await page.goto('/wolfbet/sessions/1/6');
	await page.getByRole('button', { name: 'Comments on bet 12' }).click();
	await expect(page.getByRole('region', { name: 'Comments on bet 12' })).toContainText(
		'on the bet'
	);
});

test('a live change reloads the list, and a refused post says why', async ({ page }) => {
	const { comments, socket } = await rig(page, {
		post: () => ({
			status: 429,
			json: { detail: "You're commenting too fast. Wait a minute and try again." }
		})
	});
	await page.goto('/wolfbet/sessions/1/6');
	await expect(sessionBlock(page)).toContainText('No comments yet.');
	comments.push(comment(5, 'from elsewhere', false));
	socket().send(
		JSON.stringify({
			op: 'comment',
			topic: 'svc:wolfbet:comments',
			kind: 'session',
			epoch_id: 1,
			session_id: 6,
			bet_id: null,
			at: null
		})
	);
	await expect(sessionBlock(page)).toContainText('from elsewhere');
	await sessionBlock(page).getByLabel('Add a comment').fill('again');
	await sessionBlock(page).getByRole('button', { name: 'Post' }).click();
	await expect(sessionBlock(page)).toContainText(
		"You're commenting too fast. Wait a minute and try again."
	);
});

test('clicking the chart offers a note at that time', async ({ page, hasTouch }) => {
	await rig(page);
	await page.goto('/wolfbet');
	const notes = page.getByRole('region', { name: 'Chart notes' });
	await expect(notes).toContainText('Click the chart to add a note at that time.');
	const chart = page.getByRole('figure', { name: /Profit for all lanes/ });
	await chart.scrollIntoViewIfNeeded();
	const box = await chart.boundingBox();
	if (!box) throw new Error('chart has no box');
	const [x, y] = [box.x + box.width * 0.5, box.y + box.height * 0.5];
	await (hasTouch ? page.touchscreen.tap(x, y) : page.mouse.click(x, y));
	const field = notes.getByLabel(/Add a note at/);
	await field.fill('spike here');
	await notes.getByRole('button', { name: 'Post' }).click();
	await expect(notes.getByRole('listitem')).toContainText('spike here');
});
