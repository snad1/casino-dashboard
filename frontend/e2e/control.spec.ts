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
const done = { status: 200, json: { status: 'succeeded', exit_code: 0, job_id: null } };

interface Sent {
	path: string;
	body: unknown;
	key: string | undefined;
}

async function rig(page: Page, me: object, handlers: Record<string, Handler> = {}) {
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
		...handlers
	});
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op !== 'subscribe') return;
			if (message.topic === 'svc:wolfbet') ws.send(JSON.stringify(SNAPSHOT));
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
				output: 'CLI Bot: RUNNING\nTelegram Bot: RUNNING\nWeb Admin: stopped'
			}
		})
	});
	await page.goto('/wolfbet/control');
	await services(page, 'Telegram bot').getByRole('button', { name: 'Restart' }).click();
	const dialog = page.getByRole('dialog', { name: 'Restart the Telegram bot?' });
	await dialog.getByRole('button', { name: 'Restart' }).click();
	await expect(page.getByText('Restart: done.')).toBeVisible();

	await services(page, 'All services').getByRole('button', { name: 'Check' }).click();
	await expect(page.getByLabel('All services status')).toContainText('Web Admin: stopped');
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
			services(page, 'Old web admin').getByRole('button', { name, exact: true })
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
	await rig(page, adminMe, {
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

test('the header kill switch pauses every lane from the live page', async ({ page }) => {
	const sent = await rig(page, operatorMe, { 'POST /wolfbet/control/pause': () => done });
	await page.goto('/wolfbet');
	const killSwitch = page.getByRole('group', { name: 'Kill switch' });
	await expect(killSwitch.getByRole('button', { name: 'Stop all' })).toBeDisabled();
	await killSwitch.getByRole('button', { name: 'Pause all' }).click();
	await page
		.getByRole('dialog', { name: 'Pause all lanes?' })
		.getByRole('button', { name: 'Pause all lanes' })
		.click();
	await expect(page.getByText('Pause all lanes: done.')).toBeVisible();
	expect(sent.map((s) => s.body)).toEqual([{ target: 'all' }]);
});
