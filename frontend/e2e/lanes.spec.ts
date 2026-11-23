import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { adminMe, controlMenu, operatorMe, stubApi, viewerMe, type Handler } from './stub';

const JOB_ID = 'cd'.repeat(16);
const CONFIG = {
	keys: [],
	lane_count: 3,
	override_lanes: { min: 2, max: 10 },
	split_choices: ['none', 'budget', 'bet', 'both'],
	lane_split: 'none',
	base: { base_bet: 0.0002 },
	lanes: {}
};
const DIFF = {
	base: [
		{ key: 'lanes', old: 3, new: 2, apply: 'restart', removed: false },
		{ key: 'lane_split', old: 'none', new: 'both', apply: 'restart', removed: false }
	],
	lanes: {},
	warnings: []
};
const ARCHIVES = [
	'  .wolfbet_autobot-20261101-120000.db  168 sessions  2026-10-01T09:00 → 2026-11-01T11:59  4.2M',
	'',
	'  Total: 4.2 MB'
].join('\n');
const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: {
		1: { status: 'running', balance: 1 },
		2: { status: 'running', balance: 1, name: 'friend-A' },
		3: { status: 'stopped', balance: 1 }
	},
	tape: []
};
const done = (output: string | null = null) => ({
	status: 200,
	json: { status: 'succeeded', exit_code: 0, job_id: null, output }
});

interface Rig {
	posts: Array<{ path: string; body: unknown }>;
	subscribes: string[];
	socket: () => WebSocketRoute;
}

async function rig(page: Page, me: object, handlers: Record<string, Handler> = {}): Promise<Rig> {
	const posts: Rig['posts'] = [];
	const subscribes: string[] = [];
	page.on('request', (request) => {
		const path = new URL(request.url()).pathname.replace('/api', '');
		if (request.method() === 'POST' && path.startsWith('/wolfbet/')) {
			posts.push({ path, body: request.postDataJSON() });
		}
	});
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/config': () => ({ status: 200, json: CONFIG }),
		'POST /wolfbet/control/archives': () => done(ARCHIVES),
		'POST /wolfbet/config/preview': () => ({ status: 200, json: DIFF }),
		...handlers
	});
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		socket = ws;
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op !== 'subscribe') return;
			subscribes.push(message.topic);
			if (message.topic === 'svc:wolfbet') ws.send(JSON.stringify(SNAPSHOT));
			if (message.topic === `svc:wolfbet:job:${JOB_ID}`) {
				const topic = message.topic;
				ws.send(
					JSON.stringify({
						op: 'job',
						topic,
						seq: 0,
						stream: 'stdout',
						line: 'Lanes set to 2 - split: both.'
					})
				);
				ws.send(JSON.stringify({ op: 'job_end', topic, status: 'succeeded', exit_code: 0 }));
			}
		});
	});
	return {
		posts,
		subscribes,
		socket: () => {
			if (!socket) throw new Error('socket was never opened');
			return socket;
		}
	};
}

test('a preview shows the diff, each lane share and which lanes stop, then the change runs', async ({
	page
}) => {
	const { posts, subscribes } = await rig(page, operatorMe, {
		'POST /wolfbet/control/lanes': () => ({
			status: 202,
			json: { status: 'running', exit_code: null, job_id: JOB_ID, output: null }
		}),
		[`GET /wolfbet/jobs/${JOB_ID}`]: () => ({
			status: 200,
			json: {
				job_id: JOB_ID,
				cmd: 'wolfbet.lanes',
				status: 'succeeded',
				exit_code: 0,
				duration_ms: 3000,
				lines: 1
			}
		})
	});
	await page.goto('/wolfbet/lanes');
	await expect(page.getByText('Now 3 lanes, split none.')).toBeVisible();
	await expect(page.getByText('No changes.')).toBeVisible();
	await page.getByRole('spinbutton', { name: 'Lanes' }).fill('2');
	await page.getByRole('combobox', { name: 'Split' }).selectOption('both');
	await expect(page.getByRole('button', { name: 'Change lanes' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Preview' }).click();

	await expect(page.getByRole('region', { name: 'Changes' })).toContainText('lanes32restart');
	const shares = page.getByRole('table', { name: 'Lane shares' });
	await expect(shares.getByRole('row', { name: 'Lane 1 0.0001 0.50' })).toBeVisible();
	await expect(shares.getByRole('row', { name: 'Lane 2 0.0001 0.50' })).toBeVisible();
	await expect(page.getByText('Running lanes keep betting.')).toBeVisible();
	await expect(page.getByText('lane 3 stops.')).toBeVisible();

	await page.getByRole('button', { name: 'Change lanes' }).click();
	const dialog = page.getByRole('dialog', { name: 'Change lanes to 2?' });
	await dialog.getByRole('button', { name: 'Change lanes' }).click();
	const drawer = page.getByRole('dialog', { name: 'Job output' });
	await expect(drawer.getByText('Lanes set to 2 - split: both.')).toBeVisible();
	await expect.poll(() => subscribes.filter((t) => t === 'svc:wolfbet').length).toBe(2);

	expect(posts.find((p) => p.path === '/wolfbet/config/preview')?.body).toEqual({
		changes: [
			{ key: 'lanes', value: '2', lane: null },
			{ key: 'lane_split', value: 'both', lane: null }
		]
	});
	expect(posts.find((p) => p.path === '/wolfbet/control/lanes')?.body).toEqual({
		count: 2,
		split: 'both'
	});
});

test('a lane is added with a name and the settings of another lane', async ({ page }) => {
	const { posts } = await rig(page, operatorMe, { 'POST /wolfbet/control/lane_add': () => done() });
	await page.goto('/wolfbet/lanes');
	const each = page.getByRole('region', { name: 'Each lane' });
	await each.getByRole('textbox', { name: 'Name', exact: true }).fill('night run');
	await each.getByRole('combobox', { name: 'Copy settings from' }).selectOption('2');
	await each.getByRole('button', { name: 'Add lane' }).click();
	const dialog = page.getByRole('dialog', { name: 'Add the lane night run?' });
	await dialog.getByRole('button', { name: 'Add lane' }).click();
	await expect.poll(() => posts.some((p) => p.path === '/wolfbet/control/lane_add')).toBe(true);
	expect(posts.find((p) => p.path === '/wolfbet/control/lane_add')?.body).toEqual({
		name: 'night run',
		copy_from: 2
	});
});

test('a bad lane name keeps Add lane and Rename disabled', async ({ page }) => {
	await rig(page, operatorMe);
	await page.goto('/wolfbet/lanes');
	const each = page.getByRole('region', { name: 'Each lane' });
	await each.getByRole('textbox', { name: 'Name', exact: true }).fill('-x');
	await expect(each.getByRole('button', { name: 'Add lane' })).toBeDisabled();
	await expect(each.getByText('not starting with - or a space').first()).toBeVisible();
	const two = each.getByRole('listitem', { name: 'Lane 2 · friend-A' });
	await two.getByRole('textbox', { name: 'New name' }).fill('a;b');
	await expect(two.getByRole('button', { name: 'Rename' })).toBeDisabled();
});

test('each lane is renamed, restarted or removed on its own; lane 1 has no Remove', async ({
	page
}) => {
	const { posts } = await rig(page, operatorMe, {
		'POST /wolfbet/control/lane_rename': () => done(),
		'POST /wolfbet/control/lane_remove': () => done()
	});
	await page.goto('/wolfbet/lanes');
	const list = page.getByRole('list', { name: 'Lane controls' });
	await expect(list.getByRole('listitem')).toHaveCount(3);
	const one = list.getByRole('listitem', { name: 'Lane 1' });
	await expect(one.getByRole('button', { name: 'Restart' })).toBeVisible();
	await expect(one.getByRole('button', { name: 'Remove' })).toHaveCount(0);

	const two = list.getByRole('listitem', { name: 'Lane 2 · friend-A' });
	await expect(two.getByRole('button', { name: 'Rename' })).toBeDisabled();
	await two.getByRole('textbox', { name: 'New name' }).fill('night-run');
	await two.getByRole('button', { name: 'Rename' }).click();
	await page
		.getByRole('dialog', { name: 'Rename lane 2 to night-run?' })
		.getByRole('button', { name: 'Rename' })
		.click();
	await expect.poll(() => posts.some((p) => p.path === '/wolfbet/control/lane_rename')).toBe(true);

	await list
		.getByRole('listitem', { name: 'Lane 3' })
		.getByRole('button', { name: 'Remove' })
		.click();
	await page
		.getByRole('dialog', { name: 'Remove lane 3?' })
		.getByRole('button', { name: 'Remove lane 3' })
		.click();
	await expect.poll(() => posts.some((p) => p.path === '/wolfbet/control/lane_remove')).toBe(true);
	expect(posts.find((p) => p.path === '/wolfbet/control/lane_rename')?.body).toEqual({
		lane: 2,
		name: 'night-run'
	});
	expect(posts.find((p) => p.path === '/wolfbet/control/lane_remove')?.body).toEqual({ lane: 3 });
});

test('archives are listed and resets stay admin-only', async ({ page }) => {
	await rig(page, operatorMe);
	await page.goto('/wolfbet/lanes');
	const archives = page.getByRole('table', { name: 'Archived databases' });
	await expect(archives).toContainText('.wolfbet_autobot-20261101-120000.db');
	await expect(archives).toContainText('168');
	await expect(page.getByText('Total 4.2 MB')).toBeVisible();
	const resets = page.getByRole('region', { name: 'Resets' });
	await expect(resets.getByRole('button', { name: 'Reset statistics' })).toBeDisabled();
	await expect(resets.getByText('Needs admin role').first()).toBeVisible();
	await expect(resets).toContainText('all lanes at once');
});

test('an admin reset needs the typed service name', async ({ page }) => {
	await rig(page, adminMe, { 'POST /wolfbet/control/reset': () => done() });
	await page.goto('/wolfbet/lanes');
	await page
		.getByRole('region', { name: 'Resets' })
		.getByRole('button', { name: 'Reset statistics' })
		.click();
	const dialog = page.getByRole('dialog', { name: 'Reset statistics for wolfbet?' });
	const confirm = dialog.getByRole('button', { name: 'Reset statistics' });
	await expect(confirm).toBeDisabled();
	await dialog.getByLabel('Type wolfbet to confirm').fill('wolfbet');
	await confirm.click();
	await expect(page.getByText('Reset statistics: done.')).toBeVisible();
});

test('a refused lane count shows the bot message and nothing runs', async ({ page }) => {
	const { posts } = await rig(page, operatorMe, {
		'POST /wolfbet/config/preview': () => ({ status: 422, json: { detail: 'lanes must be 1-10' } })
	});
	await page.goto('/wolfbet/lanes');
	await page.getByRole('spinbutton', { name: 'Lanes' }).fill('11');
	await page.getByRole('button', { name: 'Preview' }).click();
	await expect(page.getByText('lanes must be 1-10')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Change lanes' })).toHaveCount(0);
	expect(posts.map((p) => p.path)).not.toContain('/wolfbet/control/lanes');
});

test('a viewer is told which role the lanes editor needs', async ({ page }) => {
	await rig(page, viewerMe);
	await page.goto('/wolfbet/lanes');
	await expect(page.getByRole('region', { name: 'Lanes and split' })).toContainText(
		'Needs operator role'
	);
	await expect(page.getByRole('spinbutton', { name: 'Lanes' })).toHaveCount(0);
});

test('/wolfbet/lanes has no serious accessibility issues', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await rig(page, operatorMe);
	await page.goto('/wolfbet/lanes');
	await expect(page.getByRole('table', { name: 'Archived databases' })).toBeVisible();
	const result = await new AxeBuilder({ page })
		.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
		.analyze();
	const blocking = result.violations
		.filter((v) => v.impact === 'serious' || v.impact === 'critical')
		.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
	expect(blocking).toEqual([]);
});
