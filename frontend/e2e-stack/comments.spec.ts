import { expect, test } from '@playwright/test';
import { signIn } from './stack';

test('a comment shows up live for another viewer', async ({ browser, page }) => {
	const text = `seen at ${Date.now()}`;
	await signIn(page);
	await page.getByRole('link', { name: /#5 · Lane 2/ }).click();
	const other = await browser.newPage({ ignoreHTTPSErrors: true });
	await signIn(other);
	await other.getByRole('link', { name: /#5 · Lane 2/ }).click();
	const theirs = other.getByRole('region', { name: 'Session comments' });
	await expect(theirs).toBeVisible();

	const mine = page.getByRole('region', { name: 'Session comments' });
	await mine.getByLabel('Add a comment').fill(text);
	await mine.getByRole('button', { name: 'Post' }).click();
	await expect(mine.getByRole('listitem').filter({ hasText: text })).toBeVisible();
	await expect(theirs.getByRole('listitem').filter({ hasText: text })).toBeVisible();
	await expect(theirs.getByRole('listitem').filter({ hasText: text })).not.toContainText('Edit');
	await other.close();
});
