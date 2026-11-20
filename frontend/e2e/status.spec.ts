import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { adminMe, stubApi, viewerMe } from './stub';

const STATUS = {
	at: '2026-11-20T10:00:00Z',
	checks: [
		{
			name: 'Live stream',
			ok: true,
			value: 2.5,
			unit: 's',
			detail: 'Seconds since the last bot event.'
		},
		{
			name: 'Tick duration',
			ok: false,
			value: 0.25,
			unit: 's',
			detail: 'Longest live update lately.'
		},
		{ name: 'Live viewers', ok: true, value: 7, unit: 'count', detail: 'Open live connections.' },
		{ name: 'Host agent', ok: true, value: null, unit: 's', detail: 'Round trip to the agent.' }
	]
};

test('an admin sees each check with its state and value, and it refreshes', async ({ page }) => {
	let calls = 0;
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 't', expires_in: 30 } }),
		'GET /status': () => {
			calls += 1;
			return { status: 200, json: STATUS };
		}
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => undefined);
	await page.goto('/admin/status');
	await expect(page.getByRole('heading', { name: 'Status' })).toBeVisible();
	await expect(page.getByText('1 need attention.')).toBeVisible();
	const tick = page.getByRole('row', { name: /Tick duration/ });
	await expect(tick.getByText('Warning')).toBeVisible();
	await expect(tick.getByText('250 ms')).toBeVisible();
	const viewers = page.getByRole('row', { name: /Live viewers/ });
	await expect(viewers.getByText('OK')).toBeVisible();
	await expect(viewers.getByText('7', { exact: true })).toBeVisible();
	await expect(
		page.getByRole('row', { name: /Host agent/ }).getByText('-', { exact: true })
	).toBeVisible();
	await expect.poll(() => calls, { timeout: 8000 }).toBeGreaterThan(1);
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test('a viewer cannot open the status page', async ({ page }) => {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 't', expires_in: 30 } })
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => undefined);
	await page.goto('/admin/status');
	await expect(page.getByText("You don't have access to this page.")).toBeVisible();
});
