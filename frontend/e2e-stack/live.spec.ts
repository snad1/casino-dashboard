import { expect, test } from '@playwright/test';
import { openDashboard } from './stack';

test('replayed bets move the ticker and the lane cards', async ({ page }) => {
	await openDashboard(page);
	const lane = page.getByRole('article', { name: 'Lane 1' });
	await expect(lane).toContainText('Running');
	const bets = async () => Number((await lane.innerText()).match(/Bets\s*(\d+)/)?.[1] ?? 0);
	const before = await bets();
	await expect.poll(bets, { timeout: 15_000 }).toBeGreaterThan(before);
	await expect(page.getByRole('banner')).toContainText(/Bets \d+/);
	await expect(page.getByRole('region', { name: 'Host' })).toContainText(
		'Host metrics unavailable'
	);
});
