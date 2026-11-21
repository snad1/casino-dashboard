import { expect, test } from '@playwright/test';
import { STATE, openDashboard } from './stack';

test('a comment shows up live for another viewer', async ({ browser, page, baseURL }) => {
	const text = `seen at ${Date.now()}`;
	await openDashboard(page);
	await page.getByRole('link', { name: /#5 · Lane 2/ }).click();
	const otherContext = await browser.newContext({
		baseURL,
		ignoreHTTPSErrors: true,
		storageState: STATE.other
	});
	const other = await otherContext.newPage();
	await openDashboard(other);
	await other.getByRole('link', { name: /#5 · Lane 2/ }).click();
	const theirs = other.getByRole('region', { name: 'Session comments' });
	await expect(theirs).toBeVisible();

	const mine = page.getByRole('region', { name: 'Session comments' });
	await mine.getByLabel('Add a comment').fill(text);
	await mine.getByRole('button', { name: 'Post' }).click();
	await expect(mine.getByRole('listitem').filter({ hasText: text })).toBeVisible();
	await expect(theirs.getByRole('listitem').filter({ hasText: text })).toBeVisible();
	await expect(theirs.getByRole('listitem').filter({ hasText: text })).not.toContainText('Edit');
	await otherContext.close();
});
