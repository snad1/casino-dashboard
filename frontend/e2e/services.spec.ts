import { expect, test, type Page } from '@playwright/test';
import { adminMe, stubApi, WOLFBET } from './stub';

const GOATED = { name: 'goated', label: 'Goated', ctl: 'goatedctl', installed: true, live: true };

async function rig(page: Page, me: object, list: object[]) {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'GET /services': () => ({ status: 200, json: list })
	});
}

test('picking a service opens the same page for it and is kept in this browser', async ({
	page
}) => {
	await rig(page, adminMe, [WOLFBET, GOATED]);
	await page.goto('/wolfbet/tools');
	const switcher = page.getByLabel('Service');
	await expect(switcher).toHaveValue('wolfbet');
	await switcher.selectOption('goated');
	await expect(page).toHaveURL('/goated/tools');
	await expect(page.getByLabel('Service')).toHaveValue('goated');
	await page.goto('/');
	await expect(page).toHaveURL('/goated');
});

test('one service shows no switcher', async ({ page }) => {
	await rig(page, adminMe, [WOLFBET]);
	await page.goto('/wolfbet/tools');
	await expect(page.locator('header').getByText('WolfBet', { exact: true })).toBeVisible();
	await expect(page.getByLabel('Service')).toHaveCount(0);
});

test('a friend with a role on one service sees only it and no access elsewhere', async ({
	page
}) => {
	const friend = { ...adminMe, roles: [{ service: 'wolfbet', role: 'viewer' }] };
	await rig(page, friend, [WOLFBET]);
	await page.goto('/');
	await expect(page).toHaveURL('/wolfbet');
	await page.goto('/goated');
	await expect(
		page.getByRole('heading', { name: "You don't have access to this page." })
	).toBeVisible();
	await expect(page.getByLabel('Service')).toHaveCount(0);
});

test('a bot that is not installed or quiet says so in the switcher', async ({ page }) => {
	await rig(page, adminMe, [
		{ ...WOLFBET, live: false },
		{ ...GOATED, installed: false, live: false }
	]);
	await page.goto('/wolfbet/tools');
	const options = page.getByLabel('Service').locator('option');
	await expect(options).toHaveText(['WolfBet (no events)', 'Goated (not installed)']);
	await expect(page.getByText('WolfBet: no events')).toBeAttached();
});

test('with no service at all the home page says so', async ({ page }) => {
	const nobody = { ...adminMe, roles: [] };
	await rig(page, nobody, []);
	await page.goto('/');
	await expect(
		page.getByRole('heading', { name: "You don't have a role on any service yet." })
	).toBeVisible();
});
