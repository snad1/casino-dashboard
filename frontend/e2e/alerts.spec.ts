import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { adminMe, controlMenu, stubApi, viewerMe, type Handler } from './stub';

const RULE = {
	id: 6,
	name: 'Bot down',
	kind: 'down',
	lane: null,
	params: { statuses: ['down'], for_s: 60 },
	cooldown_s: 600,
	enabled: true
};
const EVENTS = {
	items: [
		{
			id: 3,
			kind: 'down',
			lane: 1,
			value: null,
			message: 'Bot down: bot down for 60 s on lane 1',
			fired_at: '2026-11-20T02:00:00Z'
		}
	],
	next_before: null
};
const CHANNELS = {
	email_enabled: false,
	telegram_enabled: false,
	telegram_linked: false,
	telegram_available: true,
	quiet_start: null,
	quiet_end: null
};

interface Sent {
	path: string;
	body: unknown;
}

async function rig(page: Page, me: object, extra: Record<string, Handler> = {}): Promise<Sent[]> {
	const sent: Sent[] = [];
	page.on('request', (request) => {
		if (request.method() === 'GET') return;
		const path = new URL(request.url()).pathname.replace(/^\/api/, '');
		sent.push({ path, body: request.postData() ? request.postDataJSON() : null });
	});
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/alerts/rules': () => ({ status: 200, json: [RULE] }),
		'GET /wolfbet/alerts/events': () => ({ status: 200, json: EVENTS }),
		'GET /alerts/me/channels': () => ({ status: 200, json: CHANNELS }),
		...extra
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => undefined);
	return sent;
}

test('an admin creates a rule with dropdowns and confirms the save', async ({ page }) => {
	const sent = await rig(page, adminMe, {
		'POST /wolfbet/alerts/rules': (body) => ({
			status: 201,
			json: { ...RULE, id: 7, ...(body as object) }
		})
	});
	await page.goto('/alerts');
	await expect(page.getByRole('table', { name: 'Rules' })).toContainText('Bot down for 1m 0s');
	await expect(page.getByRole('table', { name: 'Alert history' })).toContainText(
		'Bot down: bot down for 60 s on lane 1'
	);

	await page.getByRole('button', { name: 'New rule' }).click();
	await page.getByLabel('Alert when').selectOption('drawdown');
	await page.getByLabel('Below peak by').selectOption('20');
	await page.getByLabel('Cooldown').selectOption('3600');
	await page.getByRole('combobox', { name: 'Lane' }).selectOption('2');
	await page.getByRole('button', { name: 'Save', exact: true }).click();

	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('Save rule?');
	await expect(dialog).toContainText('Watches: Balance 20 % below its peak');
	await dialog.getByRole('button', { name: 'Save rule' }).click();
	await expect(page.getByText('Rule saved.')).toBeVisible();
	expect(sent.find((s) => s.path === '/wolfbet/alerts/rules')?.body).toEqual({
		name: 'Drawdown from peak',
		kind: 'drawdown',
		lane: 2,
		params: { pct: 20 },
		cooldown_s: 3600,
		enabled: true
	});
});

test('a custom value that is not a number is refused before sending', async ({ page }) => {
	const sent = await rig(page, adminMe);
	await page.goto('/alerts');
	await page.getByRole('button', { name: 'New rule' }).click();
	await page.getByLabel('For at least').selectOption('custom');
	await page.getByLabel('Custom for at least').fill('soon');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(page.getByText('For at least must be a number above 0.')).toBeVisible();
	expect(sent.filter((s) => s.path.includes('/alerts/rules'))).toEqual([]);
});

test('test-fire reports where it went, or why it could not', async ({ page }) => {
	let channels: string[] = [];
	await rig(page, adminMe, {
		'POST /wolfbet/alerts/rules/6/test': () =>
			channels.length
				? { status: 200, json: { channels } }
				: {
						status: 422,
						json: { detail: 'Turn on email or Telegram in your alert settings first.' }
					}
	});
	await page.goto('/alerts');
	await page.getByRole('button', { name: 'Test Bot down' }).click();
	await expect(
		page.getByText('Turn on email or Telegram in your alert settings first.')
	).toBeVisible();
	channels = ['email', 'telegram'];
	await page.getByRole('button', { name: 'Test Bot down' }).click();
	await expect(page.getByText('Test sent to email and Telegram.')).toBeVisible();
});

test('viewers see rules and history without the edit buttons', async ({ page }) => {
	await rig(page, viewerMe);
	await page.goto('/alerts');
	await expect(page.getByRole('table', { name: 'Rules' })).toContainText('Bot down');
	for (const name of ['New rule', 'Edit Bot down', 'Test Bot down', 'Delete Bot down']) {
		await expect(page.getByRole('button', { name })).toHaveCount(0);
	}
});

test('a user turns email on, sets quiet hours and gets a Telegram link', async ({ page }) => {
	const sent = await rig(page, viewerMe, {
		'PUT /alerts/me/channels': (body) => ({
			status: 200,
			json: { ...CHANNELS, ...(body as object) }
		}),
		'POST /alerts/me/telegram-link': () => ({
			status: 200,
			json: { url: 'https://t.me/casino_alert_bot?start=abc', expires_at: '2026-11-20T14:10:00Z' }
		})
	});
	await page.goto('/alerts/settings');
	await page.getByRole('button', { name: 'Link Telegram' }).click();
	await expect(page.getByRole('link', { name: 'Open the alert bot' })).toHaveAttribute(
		'href',
		'https://t.me/casino_alert_bot?start=abc'
	);
	await expect(page.getByText('before 14:10 UTC')).toBeVisible();
	await expect(page.getByLabel(/Telegram alerts/)).toBeDisabled();

	await page.getByLabel(/Email alerts to/).check();
	await page.getByLabel('Quiet hours').check();
	await page.getByLabel('From').fill('22:30');
	await page.getByRole('button', { name: 'Save' }).click();
	await expect(page.getByText('Alert settings saved.')).toBeVisible();
	expect(sent.find((s) => s.path === '/alerts/me/channels')?.body).toEqual({
		email_enabled: true,
		telegram_enabled: false,
		quiet_start: '22:30:00',
		quiet_end: '07:00:00'
	});
});

test('without an alert bot the page says so', async ({ page }) => {
	await rig(page, viewerMe, {
		'GET /alerts/me/channels': () => ({
			status: 200,
			json: { ...CHANNELS, telegram_available: false }
		})
	});
	await page.goto('/alerts/settings');
	await expect(page.getByText('Telegram alerts are not set up on this dashboard.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Link Telegram' })).toHaveCount(0);
});

for (const path of ['/alerts', '/alerts/settings']) {
	test(`${path} has no serious accessibility issues`, async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await rig(page, adminMe);
		await page.goto(path);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		await expect(page.getByText(/Loading/)).toHaveCount(0);
		const result = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
			.analyze();
		const blocking = result.violations
			.filter((v) => v.impact === 'serious' || v.impact === 'critical')
			.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
		expect(blocking).toEqual([]);
	});
}
