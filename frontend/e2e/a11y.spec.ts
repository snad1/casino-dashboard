import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { adminMe, controlMenu, stubApi } from './stub';

const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: {
		1: {
			status: 'running',
			session_id: 6,
			profit: 0.5,
			total_bets: 10,
			bps_1s: 3,
			current_streak: 2,
			currency: 'btc',
			base_bet: 0.0001,
			current_bet: 0.0002,
			lane_split: 'even'
		},
		2: { status: 'stopped', profit: -0.1, total_bets: 4, stop_reason: 'Max loss reached' }
	},
	tape: [
		{
			lane: 1,
			seq: 1,
			ts_ms: Date.UTC(2026, 0, 5, 10),
			amount: 1,
			multiplier: 2,
			result: 60,
			state: 'win',
			profit: 1,
			balance: 100
		}
	]
};
const SESSION = {
	epoch_id: 1,
	id: 6,
	lane: 1,
	started_at: '2026-01-05T09:00:00Z',
	ended_at: null,
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

async function rig(page: Page) {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({
			status: 200,
			json: { items: [SESSION], next_cursor: null }
		}),
		'GET /wolfbet/sessions/1/6': () => ({ status: 200, json: SESSION }),
		'GET /wolfbet/sessions/1/6/bets': () => ({
			status: 200,
			json: { items: [], next_before: null }
		}),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /status': () => ({ status: 200, json: { at: '2026-11-20T10:00:00Z', checks: [] } }),
		'GET /wolfbet/config': () => ({
			status: 200,
			json: {
				keys: [
					{
						key: 'max_loss',
						type: 'number',
						help: 'Stop after this loss',
						off_allowed: true,
						live_reload: true,
						lane_overridable: true
					}
				],
				lane_count: 2,
				override_lanes: { min: 2, max: 10 },
				split_choices: ['none', 'budget'],
				lane_split: 'none',
				base: { max_loss: 0.02 },
				lanes: {}
			}
		}),
		'GET /admin/users': () => ({ status: 200, json: { items: [] } }),
		'GET /audit': () => ({ status: 200, json: { items: [], next_before_id: null } })
	});
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		ws.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.op === 'subscribe' && message.topic === 'svc:wolfbet') {
				ws.send(JSON.stringify(SNAPSHOT));
			}
		});
	});
}

const heading = (name: string) => (page: Page) =>
	page.getByRole('heading', { level: 1, name, exact: true });
const PAGES: Array<[string, (page: Page) => ReturnType<Page['getByRole']>]> = [
	['/login', heading('Sign in')],
	['/reset', heading('Reset your password')],
	['/offline.html', heading("You're offline")],
	['/wolfbet', (page) => page.getByRole('article', { name: 'Lane 1' })],
	['/wolfbet/sessions/1/6', heading('Session 6')],
	['/wolfbet/control', heading('Control')],
	['/wolfbet/config', heading('Config')],
	['/wolfbet/analytics', heading('Analytics')],
	['/wolfbet/terminal', heading("You don't have access to this page.")],
	['/alerts', heading('Alerts')],
	['/admin/users', heading('Users')],
	['/admin/audit', heading('Audit log')],
	['/admin/status', heading('Status')]
];

for (const scheme of ['dark', 'light'] as const) {
	for (const [path, ready] of PAGES) {
		test(`${path} has no serious accessibility issues (${scheme})`, async ({ page }) => {
			await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
			await rig(page);
			await page.goto(path);
			await expect(ready(page)).toBeVisible();
			const result = await new AxeBuilder({ page })
				.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
				.analyze();
			const blocking = result.violations
				.filter((v) => v.impact === 'serious' || v.impact === 'critical')
				.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
			expect(blocking).toEqual([]);
		});
	}
}
