import { expect, test } from '@playwright/test';
import { compose, signIn } from './stack';

test('the live view reconnects after the api restarts', async ({ page, isMobile }) => {
	test.skip(isMobile, 'one restart per run is enough');
	test.setTimeout(120_000);
	await signIn(page);
	const status = page.getByRole('banner').getByRole('status');
	compose('restart', 'api');
	await expect(status).toHaveText(/Reconnecting|Stale/, { timeout: 30_000 });
	await expect(status).toHaveText('Live', { timeout: 60_000 });
	await expect(page.getByRole('article', { name: 'Lane 1' })).toBeVisible();
});
