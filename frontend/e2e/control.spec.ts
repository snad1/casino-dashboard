import { expect, test, type Page } from '@playwright/test';
import { adminMe, controlMenu, operatorMe, stubApi, viewerMe, type Handler } from './stub';

const JOB_ID = 'ab'.repeat(16);
const BUSY = 'Another control action is running. Try again when it finishes.';
const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: { 1: { status: 'running' }, 2: { status: 'paused' } },
	tape: []
};
const job = (status: string, exit_code: number | null = null) => ({
	job_id: JOB_ID,
	cmd: 'wolfbet.update',
	status,
	exit_code,
	duration_ms: 4200,
	lines: 2
});
const CONFIG = {
	keys: [],
	lane_count: 2,
	lane_ids: [1, 2],
	override_lanes: { min: 2, max: 10 },
	split_choices: ['none'],
	lane_split: 'none',
	base: {},
	lanes: {},
	pending: {},
	rollout: {}
};
const done = { status: 200, json: { status: 'succeeded', exit_code: 0, job_id: null } };

interface Sent {
	path: string;
	body: unknown;
	key: string | undefined;
}

async function rig(
	page: Page,
	me: object,
	handlers: Record<string, Handler> = {},
	snapshot: object = SNAPSHOT
) {
	const sent: Sent[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (request.method() === 'POST' && url.pathname.startsWith('/api/wolfbet/control/')) {
			sent.push({
				path: url.pathname.replace('/api', ''),
				body: request.postDataJSON(),
				key: request.headers()['idempotency-key']
			});
		}
	});
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } }),
		'GET /wolfbet/config': () => ({ status: 200, json: CONFIG }),
		...handlers
	});
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op !== 'subscribe') return;
			if (message.topic === 'svc:wolfbet') ws.send(JSON.stringify(snapshot));
			if (message.topic === `svc:wolfbet:job:${JOB_ID}`) {
				const topic = message.topic;
				for (const [seq, line] of ['Pulling latest from git...', 'Restarted.'].entries()) {
					ws.send(JSON.stringify({ op: 'job', topic, seq, stream: 'stdout', line }));
				}
				ws.send(JSON.stringify({ op: 'job_end', topic, status: 'succeeded', exit_code: 0 }));
			}
		});
	});
	return sent;
}

const lane = (page: Page, name: string) =>
	page.getByRole('region', { name: 'Lanes', exact: true }).getByRole('listitem').filter({
		hasText: name
	});
const danger = (page: Page) => page.getByRole('region', { name: 'Danger zone' });

test('an operator pauses and resumes a lane and cannot reach the danger zone', async ({ page }) => {
	const sent = await rig(page, operatorMe, {
		'POST /wolfbet/control/pause': () => done,
		'POST /wolfbet/control/resume': () => done
	});
	await page.goto('/wolfbet/control');
	await lane(page, 'Lane 2').getByRole('button', { name: 'Pause' }).click();
	const dialog = page.getByRole('dialog', { name: 'Pause lane 2?' });
	await expect(dialog).toContainText('Effect of pause on lane 2.');
	await dialog.getByRole('button', { name: 'Pause lane 2' }).click();
	await expect(page.getByText('Pause lane 2: done.')).toBeVisible();

	await lane(page, 'All lanes').getByRole('button', { name: 'Resume' }).click();
	await page.getByRole('button', { name: 'Resume all lanes' }).click();
	await expect(page.getByText('Resume all lanes: done.')).toBeVisible();

	expect(sent.map((s) => [s.path, s.body])).toEqual([
		['/wolfbet/control/pause', { target: 2 }],
		['/wolfbet/control/resume', { target: 'all' }]
	]);
	expect(sent[0]?.key).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
	expect(sent[0]?.key).not.toBe(sent[1]?.key);

	const reset = danger(page).getByRole('button', { name: 'Reset statistics' });
	await expect(reset).toBeDisabled();
	await expect(danger(page).getByText('Needs admin role').first()).toBeVisible();
});

const services = (page: Page, name: string) =>
	page.getByRole('region', { name: 'Services' }).getByRole('listitem').filter({ hasText: name });

test('an operator restarts the Telegram bot and checks every service', async ({ page }) => {
	const sent = await rig(page, operatorMe, {
		'POST /wolfbet/control/tg_restart': () => done,
		'POST /wolfbet/control/status_all': () => ({
			status: 200,
			json: {
				status: 'succeeded',
				exit_code: 0,
				job_id: null,
				output: 'CLI Bot: RUNNING\nTelegram Bot: RUNNING'
			}
		})
	});
	await page.goto('/wolfbet/control');
	await services(page, 'Telegram bot').getByRole('button', { name: 'Restart' }).click();
	const dialog = page.getByRole('dialog', { name: 'Restart the Telegram bot?' });
	await dialog.getByRole('button', { name: 'Restart' }).click();
	await expect(page.getByText('Restart: done.')).toBeVisible();

	await services(page, 'All services').getByRole('button', { name: 'Check' }).click();
	await expect(page.getByLabel('All services status')).toContainText('Telegram Bot: RUNNING');
	expect(sent.map((s) => s.path)).toEqual([
		'/wolfbet/control/tg_restart',
		'/wolfbet/control/status_all'
	]);
});

test('a viewer sees the service buttons but cannot press them', async ({ page }) => {
	const sent = await rig(page, viewerMe);
	await page.goto('/wolfbet/control');
	for (const name of ['Status', 'Start', 'Stop', 'Restart']) {
		await expect(
			services(page, 'Telegram bot').getByRole('button', { name, exact: true })
		).toBeDisabled();
	}
	await expect(
		services(page, 'All services').getByRole('button', { name: 'Start all' })
	).toBeDisabled();
	expect(sent).toEqual([]);
});

test('an admin reset needs the typed service name and a fresh code, then retries once', async ({
	page
}) => {
	let resets = 0;
	const sent = await rig(page, adminMe, {
		'POST /wolfbet/control/reset': () =>
			resets++ === 0
				? {
						status: 403,
						json: {
							detail: 'Confirm with your authenticator code to continue.',
							step_up_required: true
						}
					}
				: done,
		'POST /auth/step-up': () => ({ status: 204 })
	});
	await page.goto('/wolfbet/control');
	await danger(page).getByRole('button', { name: 'Reset statistics' }).click();
	const dialog = page.getByRole('dialog', { name: 'Reset statistics for wolfbet?' });
	const confirm = dialog.getByRole('button', { name: 'Reset statistics' });
	await expect(confirm).toBeDisabled();
	await dialog.getByLabel('Type wolfbet to confirm').fill('wolfbe');
	await expect(confirm).toBeDisabled();
	await dialog.getByLabel('Type wolfbet to confirm').press('t');
	await dialog.getByLabel('Type wolfbet to confirm').press('Enter');
	expect(sent).toHaveLength(0);
	await confirm.click();

	const stepUp = page.getByRole('dialog', { name: "Confirm it's you" });
	await stepUp.getByLabel('Authenticator code').fill('123456');
	await stepUp.getByRole('button', { name: 'Confirm' }).click();
	await expect(page.getByText('Reset statistics: done.')).toBeVisible();
	expect(sent).toHaveLength(2);
	expect(sent[0]?.key).toBe(sent[1]?.key);
});

test('an update opens the job drawer and streams its output to the end', async ({ page }) => {
	let configReads = 0;
	await rig(page, adminMe, {
		'GET /wolfbet/config': () => {
			configReads += 1;
			return { status: 200, json: CONFIG };
		},
		'POST /wolfbet/control/update': () => ({
			status: 202,
			json: { status: 'running', exit_code: null, job_id: JOB_ID }
		}),
		[`GET /wolfbet/jobs/${JOB_ID}`]: () => ({ status: 200, json: job('succeeded', 0) })
	});
	await page.goto('/wolfbet/control');
	await danger(page)
		.getByRole('textbox', { name: /Branch/ })
		.fill('main');
	await danger(page).getByRole('button', { name: 'Update' }).click();
	const dialog = page.getByRole('dialog', { name: 'Update wolfbet?' });
	await dialog.getByLabel('Type wolfbet to confirm').fill('wolfbet');
	await dialog.getByRole('button', { name: 'Update' }).click();

	const drawer = page.getByRole('dialog', { name: 'Job output' });
	await expect(drawer.getByText('Pulling latest from git...')).toBeVisible();
	await expect(drawer.getByText('Restarted.')).toBeVisible();
	await expect(drawer).toContainText('succeeded, exit code 0 after 4.2 s');
	await expect.poll(() => configReads).toBe(2);
	await drawer.getByRole('button', { name: 'Close' }).click();
	await expect(drawer).toBeHidden();
});

test('a busy agent shows its message as a toast', async ({ page }) => {
	await rig(page, operatorMe, {
		'POST /wolfbet/control/start': () => ({ status: 409, json: { detail: BUSY } })
	});
	await page.goto('/wolfbet/control');
	await page
		.getByRole('region', { name: 'Service', exact: true })
		.getByRole('button', { name: 'Start', exact: true })
		.click();
	await page
		.getByRole('dialog', { name: 'Start wolfbet?' })
		.getByRole('button', { name: 'Start' })
		.click();
	await expect(page.getByText(BUSY)).toBeVisible();
});

test('the Lanes toolbar pauses every lane, and the header has no kill switch', async ({ page }) => {
	const sent = await rig(page, operatorMe, { 'POST /wolfbet/control/pause': () => done });
	await page.goto('/wolfbet');
	await expect(page.getByRole('banner').getByRole('button', { name: 'Pause all' })).toHaveCount(0);
	const killSwitch = page.getByRole('group', { name: 'All lanes' });
	await expect(killSwitch.getByRole('button', { name: 'Resume all' })).toBeVisible();
	await expect(killSwitch.getByRole('button', { name: 'Stop all' })).toBeDisabled();
	await killSwitch.getByRole('button', { name: 'Pause all' }).click();
	await page
		.getByRole('dialog', { name: 'Pause all lanes?' })
		.getByRole('button', { name: 'Pause all lanes' })
		.click();
	await expect(page.getByText('Pause all lanes: done.')).toBeVisible();
	expect(sent.map((s) => s.body)).toEqual([{ target: 'all' }]);
});

test('a lane card pauses or stops only its own lane', async ({ page }) => {
	const sent = await rig(page, operatorMe, {
		'POST /wolfbet/control/pause': () => done,
		'POST /wolfbet/control/lane_stop': () => done
	});
	await page.goto('/wolfbet');
	const one = page.getByRole('article', { name: 'Lane 1' });
	const two = page.getByRole('article', { name: 'Lane 2' });
	await expect(one.getByRole('button', { name: 'Pause' })).toBeVisible();
	await expect(one.getByRole('button', { name: 'Stop' })).toBeVisible();
	await expect(one.getByRole('button', { name: 'Restart at session end' })).toBeVisible();
	await expect(one.getByRole('button', { name: 'Restart now' })).toBeVisible();
	await expect(two.getByRole('button', { name: 'Resume' })).toBeVisible();
	await expect(two.getByRole('link', { name: 'Configure lane 2' })).toHaveAttribute(
		'href',
		'/wolfbet/config?tab=2'
	);

	await one.getByRole('button', { name: 'Pause' }).click();
	await page
		.getByRole('dialog')
		.getByRole('button', { name: /^Pause/ })
		.click();
	await two.getByRole('button', { name: 'Stop' }).click();
	await page
		.getByRole('dialog', { name: 'Stop lane 2?' })
		.getByRole('button', { name: 'Stop lane 2' })
		.click();
	await expect
		.poll(() => sent.map((s) => [s.path, s.body]))
		.toEqual([
			['/wolfbet/control/pause', { target: 1 }],
			['/wolfbet/control/lane_stop', { lane: 2 }]
		]);
});

test('each lane shows the code it runs and its waiting change, and Apply restarts it', async ({
	page
}) => {
	let config: Record<string, unknown> = {
		...CONFIG,
		pending: { '2': [{ key: 'multiplier', running: 3, saved: 4 }] },
		rollout: { '1': 'new', '2': 'waiting for session end' }
	};
	const sent = await rig(page, operatorMe, {
		'GET /wolfbet/config': () => ({ status: 200, json: config }),
		'POST /wolfbet/control/lane_restart_end': () => {
			config = { ...CONFIG, rollout: { '1': 'new', '2': 'new' } };
			return done;
		}
	});
	await page.goto('/wolfbet/control');
	await expect(lane(page, 'Lane 1').getByText('Code: new')).toBeVisible();
	await expect(lane(page, 'Lane 2').getByText('Code: waiting for session end')).toBeVisible();
	await expect(
		lane(page, 'Lane 2').getByText('Waiting for a restart: multiplier 3 -> 4')
	).toBeVisible();
	await expect(lane(page, 'Lane 1').getByText(/Waiting for a restart/)).toHaveCount(0);
	await lane(page, 'Lane 2').getByRole('button', { name: 'Apply at session end' }).click();
	await page
		.getByRole('dialog', { name: 'Restart lane 2 at its session end?' })
		.getByRole('button', { name: 'Restart at session end' })
		.click();
	await expect(lane(page, 'Lane 2').getByText('Code: new')).toBeVisible();
	await expect(lane(page, 'Lane 2').getByText(/Waiting for a restart/)).toHaveCount(0);
	expect(sent.map((s) => [s.path, s.body])).toEqual([
		['/wolfbet/control/lane_restart_end', { lane: 2 }]
	]);
});

test('a viewer neither reads the config nor sees code or waiting lines', async ({ page }) => {
	const reads: string[] = [];
	page.on('request', (request) => {
		if (new URL(request.url()).pathname === '/api/wolfbet/config') reads.push(request.url());
	});
	await rig(page, viewerMe, {
		'GET /wolfbet/config': () => ({
			status: 200,
			json: { ...CONFIG, rollout: { '1': 'old' } }
		})
	});
	await page.goto('/wolfbet/control');
	await expect(lane(page, 'Lane 1')).toBeVisible();
	await expect(page.getByText(/Code: /)).toHaveCount(0);
	expect(reads).toEqual([]);
});

test('a config that cannot be read shows one message and keeps the lane controls', async ({
	page
}) => {
	await rig(page, operatorMe, {
		'GET /wolfbet/config': () => ({
			status: 502,
			json: { detail: "Could not read the bot's config. Try again." }
		})
	});
	await page.goto('/wolfbet/control');
	await expect(page.getByText("Could not read the bot's config. Try again.")).toBeVisible();
	await expect(lane(page, 'Lane 2').getByRole('button', { name: 'Pause' })).toBeVisible();
});

test('each lane row stops a running lane and starts a lane that is down', async ({ page }) => {
	const sent = await rig(
		page,
		operatorMe,
		{
			'GET /wolfbet/config': () => ({ status: 200, json: { ...CONFIG, lane_ids: [1, 2, 3] } }),
			'POST /wolfbet/control/lane_stop': () => done,
			'POST /wolfbet/control/lane_start': () => done
		},
		{ ...SNAPSHOT, lanes: { ...SNAPSHOT.lanes, 3: { status: 'down' } } }
	);
	await page.goto('/wolfbet/control');
	await expect(
		lane(page, 'Lane 1').getByRole('button', { name: 'Start', exact: true })
	).toHaveCount(0);
	await expect(lane(page, 'Lane 3').getByRole('button', { name: 'Stop', exact: true })).toHaveCount(
		0
	);
	await lane(page, 'Lane 1').getByRole('button', { name: 'Stop', exact: true }).click();
	await page
		.getByRole('dialog', { name: 'Stop lane 1?' })
		.getByRole('button', { name: 'Stop lane 1' })
		.click();
	await expect(page.getByText('Stop lane 1: done.')).toBeVisible();
	await lane(page, 'Lane 3').getByRole('button', { name: 'Start', exact: true }).click();
	await page
		.getByRole('dialog', { name: 'Start lane 3?' })
		.getByRole('button', { name: 'Start lane 3' })
		.click();
	await expect(page.getByText('Start lane 3: done.')).toBeVisible();
	expect(sent.map((s) => [s.path, s.body])).toEqual([
		['/wolfbet/control/lane_stop', { lane: 1 }],
		['/wolfbet/control/lane_start', { lane: 3 }]
	]);
});
