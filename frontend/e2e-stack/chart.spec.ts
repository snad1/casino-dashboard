import { expect, test } from '@playwright/test';
import { signIn } from './stack';

test('zooming the chart out loads older history', async ({ page, isMobile }) => {
	test.skip(isMobile, 'wheel zoom is a desktop gesture');
	await signIn(page);
	const chart = page.getByRole('figure', { name: /Profit for all lanes/ });
	await expect(chart).toBeVisible();
	const first = await page.waitForRequest((r) => r.url().includes('/api/wolfbet/series'));
	const firstFrom = Date.parse(new URL(first.url()).searchParams.get('from') ?? '');
	const older = page.waitForRequest((r) => {
		if (!r.url().includes('/api/wolfbet/series')) return false;
		return Date.parse(new URL(r.url()).searchParams.get('from') ?? '') < firstFrom;
	});
	await chart.hover();
	for (let i = 0; i < 12; i += 1) await page.mouse.wheel(0, 400);
	await older;
});
