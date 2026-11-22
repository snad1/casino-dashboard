import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { adminMe, controlMenu, operatorMe, stubApi, viewerMe, type Handler } from './stub';

const key = (over: Record<string, unknown>) => ({
	type: 'number',
	help: null,
	choices: null,
	min: null,
	max: null,
	off_allowed: false,
	format: null,
	default: null,
	live_reload: true,
	lane_overridable: true,
	...over
});
const CONFIG = {
	keys: [
		key({ key: 'max_loss', help: 'Stop after this loss', off_allowed: true }),
		key({ key: 'multiplier', live_reload: false, min: 1.01, max: 9900 }),
		key({ key: 'game', type: 'string', choices: ['dice', 'limbo'], lane_overridable: false }),
		key({ key: 'recurring', type: 'boolean' }),
		key({
			key: 'profit_increment_scope',
			type: 'string',
			choices: ['session', 'run'],
			live_reload: false
		}),
		key({ key: 'custom_rules_text', type: 'string', format: 'rules', off_allowed: true })
	],
	lane_count: 2,
	override_lanes: { min: 2, max: 10 },
	split_choices: ['none', 'budget', 'bet', 'both'],
	lane_split: 'none',
	base: {
		max_loss: 0.02,
		multiplier: 2.01,
		game: 'dice',
		recurring: false,
		profit_increment_scope: 'session',
		custom_rules_text: ''
	},
	lanes: { '2': { multiplier: 3 } }
};
const row = (key: string, old: unknown, next: unknown, apply: string) => ({
	key,
	old,
	new: next,
	apply,
	removed: false
});
const LIVE_DIFF = { base: [row('max_loss', 0.02, 0.01, 'live')], lanes: {}, warnings: [] };
const RESTART_DIFF = {
	base: [],
	lanes: { '2': [row('multiplier', 3, 4, 'restart')] },
	warnings: ['multiplier applies after a restart']
};
const saved = { status: 200, json: { status: 'succeeded', exit_code: 0, job_id: null } };

interface Rig {
	posts: Array<{ path: string; body: unknown }>;
	socket: () => WebSocketRoute;
}

async function rig(page: Page, me: object, handlers: Record<string, Handler> = {}): Promise<Rig> {
	const posts: Rig['posts'] = [];
	page.on('request', (request) => {
		const path = new URL(request.url()).pathname.replace('/api', '');
		if (request.method() === 'POST' && /^\/wolfbet\/(config|control)\//.test(path)) {
			posts.push({ path, body: request.postDataJSON() });
		}
	});
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/config': () => ({ status: 200, json: CONFIG }),
		'POST /wolfbet/config/preview': () => ({ status: 200, json: LIVE_DIFF }),
		'POST /wolfbet/control/config_set': () => saved,
		...handlers
	});
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op === 'subscribe' && message.topic === 'svc:wolfbet') {
				const lanes = { 1: { status: 'running', last_reload: null }, 2: { status: 'running' } };
				ws.send(JSON.stringify({ op: 'snapshot', topic: 'svc:wolfbet', lanes, tape: [] }));
			}
		});
	});
	return {
		posts,
		socket: () => {
			if (!socket) throw new Error('socket was never opened');
			return socket;
		}
	};
}

const reloadTick = (keys: string[]) =>
	JSON.stringify({
		op: 'tick',
		topic: 'svc:wolfbet',
		lanes: { 1: { last_reload: { ts_ms: 1_800_000_000_000, keys } } },
		candles: [],
		tape: []
	});

test('the form is built from the schema, with lane tabs that can use the base', async ({
	page
}) => {
	await rig(page, operatorMe);
	await page.goto('/wolfbet/config');
	await expect(page.getByLabel('max_loss')).toHaveValue('0.02');
	await expect(page.getByText('Stop after this loss')).toBeVisible();
	await expect(page.getByLabel('game')).toHaveValue('dice');
	await expect(page.getByLabel('game').getByRole('option')).toHaveText(['dice', 'limbo']);
	await expect(page.getByLabel('recurring')).toHaveValue('false');
	await expect(page.getByLabel('profit_increment_scope')).toHaveValue('session');
	await expect(page.getByLabel('lane_split')).toHaveValue('none');
	await expect(page.getByText('min 1.01, max 9900')).toBeVisible();
	const multiplierRow = page.getByRole('listitem').filter({ has: page.getByLabel('multiplier') });
	await expect(multiplierRow.getByText('restart required')).toBeVisible();

	await page.getByRole('tab', { name: 'Lane 2' }).click();
	await expect(page.getByLabel('game')).toHaveCount(0);
	const laneMultiplier = page.getByRole('listitem').filter({ has: page.getByLabel('multiplier') });
	await expect(laneMultiplier.getByLabel('Use base')).not.toBeChecked();
	await expect(page.getByLabel('multiplier')).toHaveValue('3');
	await expect(page.getByLabel('max_loss')).toBeDisabled();
});

test('a change is previewed before it can be saved, then saved and confirmed by the reload', async ({
	page
}) => {
	const { posts, socket } = await rig(page, operatorMe);
	await page.goto('/wolfbet/config');
	const save = page.getByRole('button', { name: 'Save', exact: true });
	await expect(save).toBeDisabled();
	await page.getByLabel('max_loss').fill('0.01');
	await expect(save).toBeDisabled();
	await expect(page.getByText('Preview your changes before saving.')).toBeVisible();

	await page.getByRole('button', { name: 'Preview' }).click();
	const diff = page.getByRole('region', { name: 'Changes' });
	await expect(diff.getByRole('row').nth(1)).toContainText('max_loss0.020.01live');
	await expect(save).toBeEnabled();
	await expect(page.getByRole('button', { name: 'Save + restart' })).toHaveCount(0);

	await save.click();
	const dialog = page.getByRole('dialog', { name: 'Save config?' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Save config' }).click();
	await expect(page.getByText('Save config: done.')).toBeVisible();
	socket().send(reloadTick(['max_loss']));
	await expect(page.getByText('Applied on lane 1: max_loss')).toBeVisible();

	const changes = { changes: [{ key: 'max_loss', value: '0.01', lane: null }] };
	expect(posts).toEqual([
		{ path: '/wolfbet/config/preview', body: changes },
		{ path: '/wolfbet/control/config_set', body: changes }
	]);
});

test('editing after a preview asks for a new preview', async ({ page }) => {
	await rig(page, operatorMe);
	await page.goto('/wolfbet/config');
	await page.getByLabel('max_loss').fill('0.01');
	await page.getByRole('button', { name: 'Preview' }).click();
	await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
	await page.getByLabel('max_loss').fill('0.03');
	await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
	await expect(page.getByRole('region', { name: 'Changes' })).toHaveCount(0);
});

test('no reload within 10 s shows a warning', async ({ page }) => {
	await page.clock.install();
	await rig(page, operatorMe);
	await page.goto('/wolfbet/config');
	await page.getByLabel('max_loss').fill('0.01');
	await page.getByRole('button', { name: 'Preview' }).click();
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await page
		.getByRole('dialog', { name: 'Save config?' })
		.getByRole('button', { name: 'Save config' })
		.click();
	await expect(page.getByText('Save config: done.')).toBeVisible();
	await page.clock.runFor(10_500);
	await expect(page.getByText("No reload seen after 10 s. Check the bot's logs.")).toBeVisible();
});

test('a restart-required change offers save and restart as a separate confirmed step', async ({
	page
}) => {
	const { posts } = await rig(page, adminMe, {
		'POST /wolfbet/config/preview': () => ({ status: 200, json: RESTART_DIFF }),
		'POST /wolfbet/control/restart': () => ({
			status: 202,
			json: { status: 'running', exit_code: null, job_id: 'ab'.repeat(16) }
		}),
		[`GET /wolfbet/jobs/${'ab'.repeat(16)}`]: () => ({
			status: 200,
			json: {
				job_id: 'ab'.repeat(16),
				cmd: 'wolfbet.restart',
				status: 'running',
				exit_code: null,
				duration_ms: 1,
				lines: 0
			}
		})
	});
	await page.goto('/wolfbet/config');
	await page.getByRole('tab', { name: 'Lane 2' }).click();
	await page.getByLabel('multiplier').fill('4');
	await page.getByRole('button', { name: 'Preview' }).click();
	await expect(page.getByRole('region', { name: 'Changes' })).toContainText('restart required');
	await expect(page.getByText('multiplier applies after a restart')).toBeVisible();
	await page.getByRole('button', { name: 'Save + restart' }).click();
	await page
		.getByRole('dialog', { name: 'Save config?' })
		.getByRole('button', { name: 'Save config' })
		.click();
	const restart = page.getByRole('dialog', { name: 'Restart wolfbet?' });
	await expect(restart).toBeVisible();
	await restart.getByRole('button', { name: 'Restart' }).click();
	await expect(page.getByText('Restart: started.')).toBeVisible();
	await expect(page.getByText('Saved. These changes apply after a restart.')).toBeVisible();
	expect(posts.map((p) => p.path)).toEqual([
		'/wolfbet/config/preview',
		'/wolfbet/control/config_set',
		'/wolfbet/control/restart'
	]);
	expect(posts[1]?.body).toEqual({ changes: [{ key: 'multiplier', value: '4', lane: 2 }] });
});

test('a refused value shows the bot message and nothing is saved', async ({ page }) => {
	const { posts } = await rig(page, operatorMe, {
		'POST /wolfbet/config/preview': () => ({
			status: 422,
			json: { detail: 'Max loss must be a number' }
		})
	});
	await page.goto('/wolfbet/config');
	await page.getByLabel('max_loss').fill('abc');
	await page.getByRole('button', { name: 'Preview' }).click();
	await expect(page.getByText('Max loss must be a number')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
	expect(posts.map((p) => p.path)).toEqual(['/wolfbet/config/preview']);
});

test('a viewer cannot open the editor', async ({ page }) => {
	await rig(page, viewerMe);
	await page.goto('/wolfbet/config');
	await expect(
		page.getByRole('heading', { name: "You don't have access to this page." })
	).toBeVisible();
	await expect(page.getByLabel('max_loss')).toHaveCount(0);
});

test('a pasted config fills the form, and only the preview sends it', async ({ page }) => {
	const { posts } = await rig(page, operatorMe);
	await page.goto('/wolfbet/config');
	await page.getByText('Paste config').click();
	await page
		.getByLabel('Config to paste')
		.fill(
			[
				'│ api_key    │ eyJ0eXAi...MWu8 (SET) │ API bearer token │',
				'│ currency   │ usdt   │ Betting currency │',
				'│ multiplier │ 2.5    │ Payout multiplier │',
				'│ max_loss   │ off    │ Stop when loss is this │',
				'│ recurring  │ on     │ Auto-restart cycle │',
				'Lanes: 2  split: budget'
			].join('\n')
		);
	await page.getByRole('button', { name: 'Fill form' }).click();
	await expect(page.getByText('Filled 4 fields.')).toBeVisible();
	await expect(
		page.getByText(/Not set: lanes .*api_key \(secret.*currency \(not editable here\)/)
	).toBeVisible();
	await expect(page.getByLabel('multiplier')).toHaveValue('2.5');
	await expect(page.getByLabel('max_loss')).toHaveValue('');
	await expect(page.getByLabel('recurring')).toHaveValue('true');
	await expect(page.getByLabel('lane_split')).toHaveValue('budget');
	await expect(page.getByLabel('Config to paste')).toHaveValue('');
	expect(posts).toEqual([]);

	await page.getByRole('button', { name: 'Preview' }).click();
	await expect(page.getByRole('region', { name: 'Changes' })).toBeVisible();
	const [sent] = posts;
	expect(sent?.path).toBe('/wolfbet/config/preview');
	const keys = (sent?.body as { changes: Array<{ key: string }> }).changes.map((c) => c.key);
	expect(keys.sort()).toEqual(['lane_split', 'max_loss', 'multiplier', 'recurring']);
	expect(JSON.stringify(sent?.body)).not.toContain('eyJ0eXAi');
});

test('custom rules are typed in a textarea and sent as one line', async ({ page }) => {
	const { posts } = await rig(page, operatorMe);
	await page.goto('/wolfbet/config');
	const rules = page.getByLabel('custom_rules_text');
	await expect(rules).toHaveJSProperty('tagName', 'TEXTAREA');
	await rules.fill('[\n  {"n": 3}\n]');
	await page.getByRole('button', { name: 'Preview' }).click();
	await expect(page.getByRole('region', { name: 'Changes' })).toBeVisible();
	expect(posts[0]?.body).toEqual({
		changes: [{ key: 'custom_rules_text', value: '[ {"n": 3} ]', lane: null }]
	});
});

test('a lane card link opens that lane tab', async ({ page }) => {
	await rig(page, operatorMe);
	await page.goto('/wolfbet/config?tab=2');
	await expect(page.getByRole('tab', { name: 'Lane 2' })).toHaveAttribute('aria-selected', 'true');
	await page.goto('/wolfbet/config?tab=9');
	await expect(page.getByRole('tab', { name: 'Base' })).toHaveAttribute('aria-selected', 'true');
});
