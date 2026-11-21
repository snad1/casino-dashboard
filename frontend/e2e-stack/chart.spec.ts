import { expect, test } from '@playwright/test';
import { openDashboard } from './stack';

const isSeries = (url: string) => url.includes('/api/wolfbet/series');
const fromOf = (url: string) => Date.parse(new URL(url).searchParams.get('from') ?? '');

test('zooming the chart out loads older history', async ({ page, isMobile }) => {
	test.skip(isMobile, 'wheel zoom is a desktop gesture');
	const first = page.waitForRequest((r) => isSeries(r.url()));
	await openDashboard(page);
	const chart = page.getByRole('figure', { name: /Profit for all lanes/ });
	await expect(chart).toBeVisible();
	const firstFrom = fromOf((await first).url());
	const older = page.waitForRequest((r) => isSeries(r.url()) && fromOf(r.url()) < firstFrom);
	await chart.hover();
	for (let i = 0; i < 12; i += 1) await page.mouse.wheel(0, 400);
	await older;
});
