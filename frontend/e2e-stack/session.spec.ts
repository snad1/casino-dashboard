import { expect, test } from '@playwright/test';
import { signIn } from './stack';

test('a past session opens with its totals and bets', async ({ page }) => {
	await signIn(page);
	await page.getByRole('link', { name: /#6 · Lane 3/ }).click();
	await expect(page.getByRole('heading', { level: 1, name: 'Session 6' })).toBeVisible();
	await expect(page.getByText('martingale')).toBeVisible();
	await expect(
		page
			.getByRole('row')
			.filter({ hasText: /Win|Loss/ })
			.first()
	).toBeVisible();
});
