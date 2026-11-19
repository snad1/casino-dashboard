import { expect, test } from '@playwright/test';
import { feed, save } from './feed';

const MINUTES = Number(process.env.SOAK_MINUTES ?? 60);
const WINDOW = Math.max(1, Math.round(MINUTES / 6));
const MAX_GROWTH = 0.2;

test('memory stays flat on a fast feed', async ({ page }) => {
	test.setTimeout((MINUTES + 5) * 60_000);
	const stop = await feed(page, 100);
	await page.goto('/wolfbet');
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
	const cdp = await page.context().newCDPSession(page);
	const samples: number[] = [];
	for (let minute = 0; minute <= MINUTES; minute += 1) {
		if (minute > 0) await page.waitForTimeout(60_000);
		await cdp.send('HeapProfiler.collectGarbage');
		const { usedSize } = await cdp.send('Runtime.getHeapUsage');
		samples.push(Math.round(usedSize / 1024));
	}
	stop();
	const mean = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length;
	const first = mean(samples.slice(1, WINDOW + 1));
	const last = mean(samples.slice(-WINDOW));
	const growth = (last - first) / first;
	save('soak', { minutes: MINUTES, heap_kb: samples, first_kb: first, last_kb: last, growth });
	expect(growth, `heap grew ${(growth * 100).toFixed(1)}%`).toBeLessThanOrEqual(MAX_GROWTH);
});
