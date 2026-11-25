import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { stubApi } from './stub';

const laneViewerMe = {
	email: 'friend@example.test',
	tg_uid: null,
	csrf_token: 'csrf-friend',
	totp_enabled: false,
	roles: [
		{ service: 'wolfbet', role: 'viewer', lane: 3 },
		{ service: 'wolfbet', role: 'viewer', lane: 12 }
	]
};

const lane = (profit: number) => ({
	status: 'running',
	profit,
	total_bets: 10,
	wins: 6,
	losses: 4,
	wagered: 20,
	currency: 'btc'
});

/** The api and the live socket as a lane-only user sees them; returns subscribed topics. */
async function laneRig(page: Page, me: object = laneViewerMe) {
	const calls = await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/epochs': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/sessions': () => ({
			status: 200,
			json: { items: [], next_cursor: null, total: 0 }
		})
	});
	const topics: string[] = [];
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op !== 'subscribe') return;
			topics.push(message.topic);
			const n = Number(String(message.topic).split(':').pop());
			ws.send(
				JSON.stringify({
					op: 'snapshot',
					topic: message.topic,
					lanes: { [n]: lane(n / 10) },
					tape: [
						{
							lane: n,
							seq: 1,
							ts_ms: Date.now() - n,
							amount: 1,
							multiplier: 2,
							result: 60,
							state: 'win',
							profit: 1,
							balance: 9
						}
					]
				})
			);
		});
	});
	return { calls, topics };
}

test('a lane-only friend sees their lanes live and nothing service-wide', async ({ page }) => {
	const { calls, topics } = await laneRig(page);
	await page.goto('/wolfbet');
	const lanes = page.getByRole('region', { name: 'Lanes' });
	await expect(lanes.getByRole('heading', { name: /Lane 3/ })).toBeVisible();
	await expect(lanes.getByRole('heading', { name: /Lane 12/ })).toBeVisible();
	await expect(lanes.getByRole('button')).toHaveCount(0);
	await expect(page.getByRole('region', { name: 'System' })).toHaveCount(0);
	await expect(page.getByRole('group', { name: 'All lanes' })).toHaveCount(0);
	expect(topics.sort()).toEqual(['svc:wolfbet:lane:12', 'svc:wolfbet:lane:3']);
	expect(calls).not.toContain('GET /wolfbet/control');

	const nav = page.getByRole('navigation', { name: 'Main' });
	if (await nav.isVisible()) {
		await expect(nav.getByRole('link')).toHaveText(['Live', 'Analytics', 'Sessions']);
	} else {
		await expect(page.getByRole('navigation', { name: 'Tabs' }).getByRole('link')).toHaveText([
			'Live',
			'Sessions'
		]);
	}

	const result = await new AxeBuilder({ page })
		.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
		.analyze();
	expect(
		result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
	).toEqual([]);
});

test('the sessions and analytics pickers list only their lanes', async ({ page }) => {
	const asked: string[] = [];
	await laneRig(page);
	await page.route('**/api/wolfbet/analytics?**', (route) => {
		asked.push(new URL(route.request().url()).searchParams.get('lane') ?? '');
		return route.fulfill({
			status: 403,
			json: { detail: 'You do not have permission to do that.' }
		});
	});
	await page.goto('/wolfbet/sessions');
	const sessionsLane = page.getByRole('combobox', { name: 'Lane' });
	await expect(sessionsLane.locator('option')).toHaveText(['Lane 3', 'Lane 12']);

	await page.goto('/wolfbet/analytics');
	const analyticsLane = page.getByRole('combobox', { name: 'Lane' });
	await expect(analyticsLane.locator('option')).toHaveText(['Lane 3', 'Lane 12']);
	await expect.poll(() => asked).toContain('3');
});

test('a friend on a key reads every lane of that key, cut by the key', async ({ page }) => {
	const keyViewerMe = {
		...laneViewerMe,
		roles: [
			{ service: 'wolfbet', role: 'viewer', lane: 8, key_ref: 'lakai' },
			{ service: 'wolfbet', role: 'viewer', lane: 9, key_ref: 'lakai' }
		]
	};
	const asked: (string | null)[] = [];
	await laneRig(page, keyViewerMe);
	await page.route('**/api/wolfbet/analytics**', (route) => {
		asked.push(new URL(route.request().url()).searchParams.get('lane'));
		return route.fulfill({
			status: 403,
			json: { detail: 'You do not have permission to do that.' }
		});
	});
	await page.goto('/wolfbet/sessions');
	const sessionsLane = page.getByRole('combobox', { name: 'Lane' });
	await expect(sessionsLane.locator('option')).toHaveText(['All lanes', 'Lane 8', 'Lane 9']);

	await page.goto('/wolfbet/analytics');
	const analyticsLane = page.getByRole('combobox', { name: 'Lane' });
	await expect(analyticsLane.locator('option')).toHaveText(['All', 'Lane 8', 'Lane 9']);
	await expect.poll(() => asked).toContain(null);
});

test('a service-wide page stays closed to a lane-only friend', async ({ page }) => {
	await laneRig(page);
	await page.goto('/wolfbet/config');
	await expect(
		page.getByRole('heading', { name: "You don't have access to this page." })
	).toBeVisible();
});
