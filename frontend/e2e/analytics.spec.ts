import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { controlMenu, operatorMe, stubApi, viewerMe, type Handler } from './stub';

const group = { sessions: 2, bets: 300, wins: 147, losses: 153, profit: 0.00012345, wagered: 0.03 };
const top = (id: number, profit: number) => ({
	id,
	game: 'dice',
	strategy: 'Flat',
	bets: 100,
	wins: 49,
	losses: 51,
	profit,
	wagered: 0.01,
	started_at: '2026-11-02T10:00:00'
});
const REPORT = {
	v: 1,
	window: { since: null, until: null, lane: null },
	lifetime: {
		sessions_total: 3,
		bets_total: 1234,
		wins_total: 605,
		losses_total: 629,
		wagered_total: 0.05,
		profit_total: -0.0002,
		best_profit: 0.00012345,
		worst_profit: -0.0003,
		sessions_in_profit: 1,
		sessions_in_loss: 2,
		win_rate_pct: 49.02755267423015
	},
	span: {
		first: '2026-11-02T10:00:00',
		last: '2026-11-03T12:00:00',
		span_sec: 93600,
		bet_sec: 3723
	},
	by_game: [{ game: 'dice', ...group }],
	by_strategy: [{ strategy: 'Flat', ...group }],
	by_lane: [
		{ lane: 'Lane 1', ...group },
		{ lane: 'Lane 2', ...group, profit: -0.0003 }
	],
	top_best: [top(7, 0.00012345)],
	top_worst: [top(9, -0.0003)]
};
const CHARTS = {
	by_hour: [
		{ hour: 9, bets: 10, wins: 5, wagered: 1, profit: 0.1 },
		{ hour: 10, bets: 10, wins: 4, wagered: 1, profit: -0.2 }
	],
	by_day: [{ day: '2026-11-02', profit: 0.1 }],
	profit_bins: [{ lo: -0.0003, hi: 0.00012345, sessions: 3 }],
	duration_bins: [{ lo_sec: 60, hi_sec: 3600, sessions: 3 }]
};
const answer = (source = 'bot') => ({
	status: 200,
	json: { source, report: REPORT, charts: CHARTS }
});

async function rig(page: Page, me: object, analytics: Handler = () => answer()) {
	const urls: URL[] = [];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/analytics': (body, url) => {
			urls.push(url);
			return analytics(body, url);
		}
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => undefined);
	return urls;
}

test('the report shows formatted like the bot and Apply filters by URL', async ({ page }) => {
	const urls = await rig(page, viewerMe);
	await page.goto('/wolfbet/analytics');
	const overall = page.getByRole('region', { name: 'Overall' });
	await expect(overall.getByText('1,234 W: 605 L: 629')).toBeVisible();
	await expect(overall.getByText('49.0%')).toBeVisible();
	await expect(overall.getByText('-0.0002')).toBeVisible();
	await expect(overall.getByText('+0.00012345')).toBeVisible();
	await expect(overall.getByText('1d 2h 0m 0s')).toBeVisible();
	await expect(overall.getByText('2026-11-02 10:00:00 to 2026-11-03 12:00:00')).toBeVisible();
	const lanes = page.getByRole('table', { name: 'By lane', exact: true });
	await expect(lanes.getByRole('row', { name: /Lane 2/ })).toContainText('-0.0003');
	await expect(page.getByRole('table', { name: 'Top 5 worst sessions' })).toContainText('9');
	await expect(page.getByRole('img', { name: /Profit by hour of day: 2 bars/ })).toBeVisible();

	await page.getByLabel('From', { exact: true }).fill('2026-11-01');
	await page.getByLabel('To', { exact: true }).fill('2026-11-15');
	await page.getByRole('combobox', { name: 'Lane' }).selectOption('2');
	await page.getByRole('button', { name: 'Apply' }).click();
	await expect(page).toHaveURL(/\/wolfbet\/analytics\?since=2026-11-01&until=2026-11-15&lane=2$/);
	await expect.poll(() => urls.at(-1)?.search).toBe('?since=2026-11-01&until=2026-11-15&lane=2');
});

test('a history answer says the bot was unreachable', async ({ page }) => {
	await rig(page, viewerMe, () => answer('history'));
	await page.goto('/wolfbet/analytics');
	await expect(page.getByText('Bot unreachable: numbers rebuilt from history.')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Overall' })).toContainText('49.0%');
});

test('a refused range shows the api message', async ({ page }) => {
	await rig(page, viewerMe, () => ({
		status: 422,
		json: { detail: 'That date range is not valid.' }
	}));
	await page.goto('/wolfbet/analytics?since=2026-11-01');
	await expect(page.getByRole('alert')).toHaveText(/That date range is not valid\./);
});

test('viewers see the export disabled; operators get links with the filter', async ({ page }) => {
	await rig(page, viewerMe);
	await page.goto('/wolfbet/analytics?lane=3');
	await expect(page.getByRole('button', { name: 'Download CSV' })).toBeDisabled();
	await expect(
		page.getByRole('region', { name: 'Export this range' }).getByText('Needs operator role')
	).toBeVisible();

	await page.unrouteAll({ behavior: 'ignoreErrors' });
	await rig(page, operatorMe);
	await page.goto('/wolfbet/analytics?since=2026-11-01&lane=3');
	await expect(page.getByRole('link', { name: 'Download CSV' })).toHaveAttribute(
		'href',
		'/api/wolfbet/analytics/export?since=2026-11-01&lane=3&format=csv'
	);
	await expect(page.getByRole('link', { name: 'Download JSON' })).toHaveAttribute(
		'href',
		'/api/wolfbet/analytics/export?since=2026-11-01&lane=3&format=json'
	);
	await expect(page.getByRole('link', { name: 'Tools > Export' })).toHaveAttribute(
		'href',
		'/wolfbet/tools/export'
	);
});

test('/wolfbet/analytics has no serious accessibility issues', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await rig(page, operatorMe);
	await page.goto('/wolfbet/analytics');
	await expect(page.getByRole('region', { name: 'Overall' })).toBeVisible();
	const result = await new AxeBuilder({ page })
		.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
		.analyze();
	const blocking = result.violations
		.filter((v) => v.impact === 'serious' || v.impact === 'critical')
		.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
	expect(blocking).toEqual([]);
});
