import { expect, test, type Page } from '@playwright/test';
import { adminMe, stubApi } from './stub';

const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: { 1: { status: 'running', session_id: 6, profit: 0.5, total_bets: 3, currency: 'btc' } },
	tape: []
};
const OTHER = {
	id: 9,
	email: 'other@example.test',
	roles: [{ service: null, role: 'viewer' }],
	disabled: false,
	totp_enabled: false,
	created_at: '2026-01-05T10:00:00Z'
};

async function rig(page: Page) {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } }),
		'GET /admin/users': () => ({ status: 200, json: { items: [OTHER] } })
	});
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		ws.onMessage((raw) => {
			if (JSON.parse(String(raw)).topic === 'svc:wolfbet') ws.send(JSON.stringify(SNAPSHOT));
		});
	});
}

/** The focused element's accessible label and whether it draws a visible focus indicator. */
async function focused(page: Page): Promise<{ label: string; visible: boolean }> {
	return page.evaluate(() => {
		const el = document.activeElement as HTMLElement | null;
		if (!el || el === document.body) return { label: '', visible: false };
		const style = getComputedStyle(el);
		const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
		const ring = style.boxShadow !== 'none';
		const label = (el.getAttribute('aria-label') ?? el.textContent ?? '').trim();
		return { label: label || el.tagName.toLowerCase(), visible: outline || ring };
	});
}

test.describe('keyboard', () => {
	test.skip(({ isMobile }) => isMobile, 'keyboard order is checked at desktop width');

	test('Tab reaches skip link, nav, chart, tape and lane controls in order, always visibly', async ({
		page
	}) => {
		await rig(page);
		await page.goto('/wolfbet');
		await expect(page.getByRole('article', { name: 'Lane 1' })).toBeVisible();
		const seen: string[] = [];
		for (let i = 0; i < 60; i += 1) {
			await page.keyboard.press('Tab');
			const { label, visible } = await focused(page);
			if (label === '' && seen.length) break;
			expect(visible, `focus on "${label}" is not visible`).toBe(true);
			seen.push(label);
		}
		const wanted = [
			'Skip to content',
			'Live totals',
			'Sign out',
			'Live',
			'profit',
			'0 new',
			'Configure lane 1'
		];
		const order = wanted.map((want) => seen.findIndex((label) => label === want));
		expect(
			order.every((index) => index >= 0),
			`focus order was: ${seen.join(' | ')}`
		).toBe(true);
		expect(order).toEqual([...order].sort((a, b) => a - b));
	});

	test('the skip link moves focus past the header', async ({ page }) => {
		await rig(page);
		await page.goto('/wolfbet');
		await expect(page.getByRole('article', { name: 'Lane 1' })).toBeVisible();
		await page.keyboard.press('Tab');
		await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
		await page.keyboard.press('Enter');
		await expect(page).toHaveURL(/#main$/);
		await page.keyboard.press('Tab');
		const inMain = await page.evaluate(() => !!document.activeElement?.closest('main'));
		expect(inMain).toBe(true);
	});

	test('Escape closes a confirm dialog and returns focus to its button', async ({ page }) => {
		await rig(page);
		await page.goto('/admin/users');
		const disable = page.getByRole('button', { name: 'Disable' });
		await disable.focus();
		await page.keyboard.press('Enter');
		const dialog = page.getByRole('dialog');
		await expect(dialog).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(dialog).toBeHidden();
		await expect(disable).toBeFocused();
	});
});
