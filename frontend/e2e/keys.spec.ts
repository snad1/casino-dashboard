import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { adminMe, controlMenu, operatorMe, stubApi, type Handler } from './stub';

const SECRET = 'wb_live_FRIEND_0123456789abcdef7777';
const row = (id: string, extra: object = {}) => ({
	id,
	label: id === 'default' ? 'Main key' : 'Friend A',
	kind: 'key',
	last4: id === 'default' ? '1111' : '7777',
	owner_user_id: null,
	created_at: '2026-11-24T10:00:00+00:00',
	rotated_at: null,
	used_by_lanes: [],
	...extra
});
const LIST = [row('default', { used_by_lanes: [1, 2] })];

async function rig(page: Page, me: object, handlers: Record<string, Handler> = {}) {
	const sent: Array<{ key: string; body: unknown }> = [];
	page.on('request', (request) => {
		const path = new URL(request.url()).pathname.replace('/api', '');
		if (path.startsWith('/wolfbet/keys') && request.method() !== 'GET') {
			sent.push({ key: `${request.method()} ${path}`, body: request.postDataJSON() });
		}
	});
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'GET /wolfbet/keys': () => ({ status: 200, json: { keys: LIST } }),
		...handlers
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => {});
	return sent;
}

test('a key is added after the authenticator code and is never shown again', async ({ page }) => {
	let tries = 0;
	const sent = await rig(page, adminMe, {
		'POST /wolfbet/keys': () =>
			++tries === 1
				? { status: 403, json: { detail: 'Confirm with a fresh code.', step_up_required: true } }
				: { status: 201, json: { key: row('friend-a'), note: null } },
		'POST /auth/step-up': () => ({ status: 204 })
	});
	await page.goto('/wolfbet/keys');
	const add = page.getByRole('region', { name: 'Add a key' });
	await add.getByRole('textbox', { name: 'Name' }).fill('Friend A');
	await expect(add.getByText('Saved as friend-a')).toBeVisible();
	await add.getByLabel('Key', { exact: true }).fill(SECRET);
	await add.getByRole('button', { name: 'Add key' }).click();
	const code = page.getByRole('dialog', { name: "Confirm it's you" });
	await code.getByLabel('Authenticator code').fill('123456');
	await code.getByRole('button', { name: 'Confirm' }).click();
	await expect(page.getByText('Friend A saved as friend-a.')).toBeVisible();
	await expect(add.getByLabel('Key', { exact: true })).toHaveValue('');
	expect(sent.map((s) => s.body)).toEqual([
		{ label: 'Friend A', kind: 'key', secret: SECRET },
		{ label: 'Friend A', kind: 'key', secret: SECRET }
	]);
	expect(await page.content()).not.toContain(SECRET);
});

test('the list shows what each key is used by, and a rotate says when it applies', async ({
	page
}) => {
	const sent = await rig(page, adminMe, {
		'POST /wolfbet/keys/default/rotate': () => ({
			status: 200,
			json: {
				key: row('default', { last4: '2222', rotated_at: '2026-11-24T11:00:00+00:00' }),
				note: 'Lanes using this key pick up the new one when they restart.'
			}
		})
	});
	await page.goto('/wolfbet/keys');
	const table = page.getByRole('table', { name: 'Saved keys' });
	await expect(table.getByRole('row', { name: /Main key/ })).toContainText('••••1111');
	await expect(table.getByRole('row', { name: /Main key/ })).toContainText('lane 1, 2');
	await table.getByRole('button', { name: 'Rotate' }).click();
	const form = page.getByRole('form', { name: 'Rotate Main key' });
	await form.getByLabel('New key').fill('wb_live_ROTATED_000000002222');
	await form.getByRole('button', { name: 'Save new key' }).click();
	await expect(
		page.getByText('Lanes using this key pick up the new one when they restart.')
	).toBeVisible();
	expect(sent).toEqual([
		{
			key: 'POST /wolfbet/keys/default/rotate',
			body: { secret: 'wb_live_ROTATED_000000002222' }
		}
	]);
});

test('a key in use is not deleted and the reason is shown', async ({ page }) => {
	await rig(page, adminMe, {
		'DELETE /wolfbet/keys/default': () => ({
			status: 409,
			json: { detail: 'This key is used by lane 1, 2. Move the lanes to another key first.' }
		})
	});
	await page.goto('/wolfbet/keys');
	await page.getByRole('button', { name: 'Delete' }).click();
	await page
		.getByRole('dialog', { name: 'Delete Main key?' })
		.getByRole('button', { name: 'Delete key' })
		.click();
	await expect(
		page.getByText('This key is used by lane 1, 2. Move the lanes to another key first.')
	).toBeVisible();
	await expect(page.getByRole('row', { name: /Main key/ })).toBeVisible();
});

test('a bad name or key keeps Add key disabled and says why', async ({ page }) => {
	await rig(page, adminMe);
	await page.goto('/wolfbet/keys');
	const add = page.getByRole('region', { name: 'Add a key' });
	await add.getByRole('textbox', { name: 'Name' }).fill('-');
	await expect(add.getByText('Use at least one letter or digit.')).toBeVisible();
	await add.getByRole('textbox', { name: 'Name' }).fill('Friend proxy');
	await add.getByRole('combobox', { name: 'Kind' }).selectOption('proxy');
	await add.getByLabel('Proxy URL').fill('ftp://u:pw@host');
	await expect(add.getByText('A proxy starts with http://, https:// or socks5://.')).toBeVisible();
	await expect(add.getByRole('button', { name: 'Add key' })).toBeDisabled();
});

test('an operator has no Keys link and the page says so', async ({ page }) => {
	await rig(page, operatorMe);
	await page.goto('/wolfbet');
	await expect(page.getByRole('link', { name: 'Keys' })).toHaveCount(0);
	await page.goto('/wolfbet/keys');
	await expect(page.getByText("You don't have access to this page.")).toBeVisible();
});

test('/wolfbet/keys has no serious accessibility issues', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await rig(page, adminMe);
	await page.goto('/wolfbet/keys');
	await expect(page.getByRole('table', { name: 'Saved keys' })).toBeVisible();
	const result = await new AxeBuilder({ page })
		.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
		.analyze();
	const blocking = result.violations
		.filter((v) => v.impact === 'serious' || v.impact === 'critical')
		.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
	expect(blocking).toEqual([]);
});
