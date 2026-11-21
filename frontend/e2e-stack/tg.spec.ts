import { expect, test } from '@playwright/test';
import { signedLink, TG_SESSIONS, TG_USERS } from './stack';

test.use({ storageState: { cookies: [], origins: [] } });
// Link sign-ins count against one limit per client IP (5 a minute), so only one project
// signs in here; the stubbed specs cover the mobile layout.
test.skip(({ isMobile }) => isMobile, 'link sign-ins are limited per IP');

const { first, second } = TG_USERS;

test('a bot link signs a Telegram user in once, to their own pages only', async ({ page }) => {
	const link = signedLink(first);
	await page.goto(link);
	await expect(page.getByRole('heading', { name: 'My bot' })).toBeVisible();
	await expect(page).toHaveURL(new RegExp(`/tg/${first}$`));

	await page.goto(`/tg/${first}/sessions`);
	const rows = page.getByRole('row').filter({ hasText: 'Lane 1' });
	await expect(rows).toHaveCount(TG_SESSIONS[first]);

	const csv = await page.request.get(`/api/tg/${first}/sessions.csv`);
	expect(csv.status()).toBe(200);
	const lines = (await csv.text()).trim().split('\n');
	expect(lines).toHaveLength(TG_SESSIONS[first] + 1);
	expect(lines.slice(1).every((line) => line.includes(`tg-${first}`))).toBe(true);

	expect((await page.request.get(`/api/tg/${second}/sessions`)).status()).toBe(403);
	await page.goto(`/tg/${second}`);
	await expect(
		page.getByRole('heading', { name: 'You do not have permission to do that.' })
	).toBeVisible();
	await page.goto('/wolfbet');
	await expect(page).toHaveURL(new RegExp(`/tg/${first}$`));

	await page.context().clearCookies();
	await page.goto(link);
	await expect(
		page.getByText('This link is invalid or expired. Send /web to the bot again.')
	).toBeVisible();
});

test('the other Telegram user sees only their own sessions', async ({ page }) => {
	await page.goto(signedLink(second));
	await expect(page.getByRole('heading', { name: 'My bot' })).toBeVisible();
	await page.goto(`/tg/${second}/sessions`);
	await expect(page.getByRole('row').filter({ hasText: 'Lane 1' })).toHaveCount(
		TG_SESSIONS[second]
	);
	expect((await page.request.get(`/api/tg/${first}/sessions.csv`)).status()).toBe(403);
});
