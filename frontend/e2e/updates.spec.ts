import { expect, test, type Page } from '@playwright/test';
import { adminMe, controlMenu, stubApi } from './stub';

const ownerMe = { ...adminMe, roles: [{ service: null, role: 'owner' }] };
const behind = { behind: 3, head: 'abc1234', latest: 'def5678', auto_update: false };

async function rig(page: Page, me: object, update: object) {
	const puts: unknown[] = [];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/update': () => ({ status: 200, json: update }),
		'PUT /wolfbet/update/auto': (body) => {
			puts.push(body);
			return { status: 200, json: { ...update, auto_update: true } };
		}
	});
	await page.goto('/wolfbet/control');
	return puts;
}

const card = (page: Page) => page.getByRole('region', { name: 'Updates' });

test('a bot behind its upstream shows the count and the Update button', async ({ page }) => {
	await rig(page, adminMe, behind);
	await expect(card(page)).toContainText('Update available: 3 commits (abc1234 to def5678).');
	await expect(card(page).getByRole('button', { name: 'Update' })).toBeVisible();
	await expect(card(page).getByRole('checkbox')).toHaveCount(0);
});

test('an up-to-date bot shows no Update button', async ({ page }) => {
	await rig(page, adminMe, { ...behind, behind: 0, latest: 'abc1234' });
	await expect(card(page)).toContainText('Up to date (abc1234).');
	await expect(card(page).getByRole('button', { name: 'Update' })).toHaveCount(0);
});

test('a failed check says so', async ({ page }) => {
	await rig(page, adminMe, { behind: null, head: null, latest: null, auto_update: false });
	await expect(card(page)).toContainText("Couldn't check for updates.");
});

test('the owner switches auto-update on', async ({ page }) => {
	const puts = await rig(page, ownerMe, behind);
	const box = card(page).getByRole('checkbox', {
		name: 'Update automatically after a green CI run on main'
	});
	await expect(box).not.toBeChecked();
	await box.click();
	await expect(box).toBeChecked();
	expect(puts).toEqual([{ enabled: true }]);
});
