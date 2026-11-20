import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { controlMenu, operatorMe, stubApi, viewerMe, type Handler } from './stub';

const JOB_ID = 'ab'.repeat(16);
const AGENT_DOWN = 'The bot host did not respond. Try again.';
const LOGS = [
	'2026-11-19 10:00:00  INFO     Session 12 opened',
	'2026-11-19 10:00:01  WARNING  [L2] Slow api call 900 ms',
	'\u001b[31m2026-11-19 10:00:02  ERROR    [L2] Bet failed\u001b[0m'
].join('\n');
const job = (cmd: string, status = 'succeeded', extra: object = {}) => ({
	job_id: JOB_ID,
	cmd: `wolfbet.${cmd}`,
	status,
	exit_code: status === 'running' ? null : 0,
	duration_ms: 900,
	lines: 2,
	...extra
});
const started = {
	status: 202,
	json: { status: 'running', exit_code: null, job_id: JOB_ID, output: null }
};

interface Rig {
	calls: string[];
	posts: Array<{ path: string; body: unknown }>;
	socket: () => WebSocketRoute;
}

async function rig(
	page: Page,
	me: object,
	handlers: Record<string, Handler> = {},
	onJobSubscribe: (ws: WebSocketRoute, topic: string) => void = () => undefined
): Promise<Rig> {
	const posts: Rig['posts'] = [];
	page.on('request', (request) => {
		const path = new URL(request.url()).pathname.replace('/api', '');
		if (request.method() === 'POST' && path.startsWith('/wolfbet/')) {
			posts.push({ path, body: request.postData() ? request.postDataJSON() : null });
		}
	});
	const calls = await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		...handlers
	});
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op === 'subscribe' && String(message.topic).includes(':job:')) {
				onJobSubscribe(ws, message.topic);
			}
		});
	});
	return {
		calls,
		posts,
		socket: () => {
			if (!socket) throw new Error('socket was never opened');
			return socket;
		}
	};
}

const send = (ws: WebSocketRoute, topic: string, seq: number, line: string, stream = 'stdout') =>
	ws.send(JSON.stringify({ op: 'job', topic, seq, stream, line }));
const end = (ws: WebSocketRoute, topic: string) =>
	ws.send(JSON.stringify({ op: 'job_end', topic, status: 'succeeded', exit_code: 0 }));

test('logs load, filter, and follow until Stop cancels the job', async ({ page }) => {
	let followTopic = '';
	const { posts, socket } = await rig(
		page,
		operatorMe,
		{
			'POST /wolfbet/control/logs': () => ({
				status: 200,
				json: { status: 'succeeded', exit_code: 0, job_id: null, output: LOGS }
			}),
			'POST /wolfbet/control/logs_follow': () => started,
			[`POST /wolfbet/jobs/${JOB_ID}/cancel`]: () => ({
				status: 200,
				json: job('logs_follow', 'cancelled')
			}),
			[`GET /wolfbet/jobs/${JOB_ID}`]: () => ({
				status: 200,
				json: job('logs_follow', 'cancelled')
			})
		},
		(_ws, topic) => (followTopic = topic)
	);
	await page.goto('/wolfbet/tools/logs');
	await page.getByRole('spinbutton', { name: 'Lines' }).fill('300');
	await page.getByRole('button', { name: 'Load' }).click();
	const list = page.getByRole('table', { name: 'Log lines' });
	await expect(list.getByText('Session 12 opened')).toBeVisible();
	await expect(list.getByText('2026-11-19 10:00:02  ERROR    [L2] Bet failed')).toBeVisible();
	await page.getByLabel('Level').selectOption('ERROR');
	await expect(list.getByRole('row')).toHaveCount(1);
	await page.getByLabel('Level').selectOption('all');
	await page.getByRole('combobox', { name: 'Lane', exact: true }).selectOption('2');
	await expect(list.getByRole('row')).toHaveCount(2);
	await page.getByRole('combobox', { name: 'Lane', exact: true }).selectOption('all');
	await page.getByLabel('Search').fill('slow');
	await expect(list.getByRole('row')).toHaveCount(1);
	await page.getByLabel('Search').fill('');

	await page.getByRole('button', { name: 'Follow' }).click();
	await expect(page.getByText('Following new lines...')).toBeVisible();
	await expect.poll(() => followTopic).toBe(`svc:wolfbet:job:${JOB_ID}`);
	send(socket(), followTopic, 0, '2026-11-19 10:00:05  INFO     Bet 99 won');
	await expect(list.getByText('Bet 99 won')).toBeVisible();
	await page.getByRole('button', { name: 'Stop', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Follow' })).toBeVisible();
	expect(posts.map((p) => [p.path, p.body])).toEqual([
		['/wolfbet/control/logs', { service: 'cli', lines: 300 }],
		['/wolfbet/control/logs_follow', { service: 'cli' }],
		[`/wolfbet/jobs/${JOB_ID}/cancel`, null]
	]);
});

test('leaving the logs page while following cancels the job', async ({ page }) => {
	const { posts } = await rig(page, operatorMe, {
		'POST /wolfbet/control/logs_follow': () => started,
		[`POST /wolfbet/jobs/${JOB_ID}/cancel`]: () => ({
			status: 200,
			json: job('logs_follow', 'cancelled')
		}),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } })
	});
	await page.goto('/wolfbet/tools/logs');
	await page.getByRole('button', { name: 'Follow' }).click();
	await expect(page.getByText('Following new lines...')).toBeVisible();
	await page
		.locator('a:visible', { hasText: /^Live$/ })
		.first()
		.click();
	await expect.poll(() => posts.map((p) => p.path)).toContain(`/wolfbet/jobs/${JOB_ID}/cancel`);
});

test('diagnose shows its report and benchmark streams its job output', async ({ page }) => {
	const { posts } = await rig(
		page,
		operatorMe,
		{
			'POST /wolfbet/control/diagnose': () => ({
				status: 200,
				json: {
					status: 'succeeded',
					exit_code: 0,
					job_id: null,
					output: 'Top blocker: api latency'
				}
			}),
			'POST /wolfbet/control/benchmark': () => started,
			[`GET /wolfbet/jobs/${JOB_ID}`]: () => ({ status: 200, json: job('benchmark') })
		},
		(ws, topic) => {
			send(ws, topic, 0, 'p50 120 ms');
			send(ws, topic, 1, 'p99 300 ms');
			end(ws, topic);
		}
	);
	await page.goto('/wolfbet/tools/health');
	await page.getByRole('button', { name: 'Diagnose', exact: true }).click();
	await expect(page.getByLabel('Diagnose report')).toHaveText('Top blocker: api latency');
	await page.getByLabel('Calls').fill('10');
	await page.getByLabel('Lanes (blank for one)').fill('2');
	await page.getByRole('button', { name: 'Benchmark' }).click();
	const output = page.getByRole('list', { name: 'Output lines' });
	await expect(output).toContainText('p50 120 ms');
	await expect(output).toContainText('p99 300 ms');
	await expect(page.getByText('wolfbet.benchmark: succeeded, exit code 0')).toBeVisible();
	expect(posts.at(-1)).toEqual({
		path: '/wolfbet/control/benchmark',
		body: { count: 10, lanes: 2 }
	});
	await expect(page.getByRole('button', { name: 'Doctor fix' })).toBeDisabled();
});

test('a backtest fills its progress bar, then shows the result', async ({ page }) => {
	const result = {
		v: 1,
		mode: 'session',
		summary: { bets: 10000, profit: 0.0123, win_rate_pct: 49.5 }
	};
	const { posts } = await rig(
		page,
		operatorMe,
		{
			'POST /wolfbet/control/backtest': () => started,
			[`GET /wolfbet/jobs/${JOB_ID}`]: () => ({ status: 200, json: job('backtest') })
		},
		(ws, topic) => {
			send(ws, topic, 0, '{"v": 1, "progress": 0.5}', 'stderr');
			send(ws, topic, 1, JSON.stringify(result));
			end(ws, topic);
		}
	);
	await page.goto('/wolfbet/tools/backtest');
	await page.getByRole('spinbutton', { name: 'Bets' }).fill('10000');
	await page.getByLabel('Seed').fill('42');
	await page.getByRole('button', { name: 'Run backtest' }).click();
	const table = page.getByRole('table', { name: 'Backtest result' });
	await expect(table.getByRole('row', { name: 'profit 0.0123' })).toBeVisible();
	await expect(table.getByRole('row', { name: 'win_rate_pct 49.5' })).toBeVisible();
	await expect(page.getByRole('progressbar', { name: 'Backtest progress' })).toHaveAttribute(
		'value',
		'1'
	);
	expect(posts.at(-1)?.body).toEqual({ simulate: 10000, seed: 42 });
});

test('an export finishes and downloads its file from the api', async ({ page }) => {
	await rig(
		page,
		operatorMe,
		{
			'POST /wolfbet/control/export': () => started,
			[`GET /wolfbet/jobs/${JOB_ID}`]: () => ({
				status: 200,
				json: job('export', 'succeeded', { file_id: 'fe'.repeat(16) })
			})
		},
		(ws, topic) => end(ws, topic)
	);
	await page.goto('/wolfbet/tools/export');
	await page.getByRole('button', { name: 'Export' }).click();
	const download = page.waitForEvent('download');
	await page.getByRole('link', { name: 'Download' }).click();
	expect((await download).url()).toMatch(
		new RegExp(`/api/wolfbet/jobs/${JOB_ID}/file\\?format=csv$`)
	);
});

test('a JSON export needs a session id before it is sent', async ({ page }) => {
	const { posts } = await rig(page, operatorMe);
	await page.goto('/wolfbet/tools/export');
	await page.getByLabel('Format').selectOption('json');
	await page.getByRole('button', { name: 'Export' }).click();
	expect(posts).toEqual([]);
});

test('a viewer sees the tools but cannot run them', async ({ page }) => {
	await rig(page, viewerMe);
	await page.goto('/wolfbet/tools/health');
	await expect(page.getByRole('button', { name: 'Diagnose', exact: true })).toBeDisabled();
	await expect(page.getByText('Needs operator role').first()).toBeVisible();
	await page.goto('/wolfbet/tools/logs');
	await expect(page.getByRole('button', { name: 'Load' })).toBeDisabled();
});

test('an agent failure shows a toast', async ({ page }) => {
	await rig(page, operatorMe, {
		'POST /wolfbet/control/logs': () => ({ status: 502, json: { detail: AGENT_DOWN } })
	});
	await page.goto('/wolfbet/tools/logs');
	await page.getByRole('button', { name: 'Load' }).click();
	await expect(page.getByText(AGENT_DOWN)).toBeVisible();
});

for (const path of ['tools', 'tools/logs', 'tools/health', 'tools/backtest', 'tools/export']) {
	test(`/wolfbet/${path} has no serious accessibility issues`, async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await rig(page, operatorMe);
		await page.goto(`/wolfbet/${path}`);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const result = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
			.analyze();
		const blocking = result.violations
			.filter((v) => v.impact === 'serious' || v.impact === 'critical')
			.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
		expect(blocking).toEqual([]);
	});
}
