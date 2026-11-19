import { expect, test } from '@playwright/test';
import { feed, percentile, save } from './feed';

const SECONDS = Number(process.env.FRAMERATE_SECONDS ?? 30);

test('frame times at 10 Hz on a laptop (1x) and a mid phone (4x CPU throttle)', async ({
	page
}) => {
	test.setTimeout((SECONDS * 2 + 60) * 1000);
	const stop = await feed(page, 10);
	await page.goto('/wolfbet');
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
	const cdp = await page.context().newCDPSession(page);
	const results: Record<string, unknown> = {};
	for (const rate of [1, 4]) {
		await cdp.send('Emulation.setCPUThrottlingRate', { rate });
		const sample = await page.evaluate(async (ms) => {
			const frames: number[] = [];
			let longTasks = 0;
			const observer = new PerformanceObserver((list) => (longTasks += list.getEntries().length));
			observer.observe({ type: 'longtask' });
			await new Promise<void>((done) => {
				let last = performance.now();
				const end = last + ms;
				const step = (now: number) => {
					frames.push(now - last);
					last = now;
					if (now < end) requestAnimationFrame(step);
					else done();
				};
				requestAnimationFrame(step);
			});
			observer.disconnect();
			return { frames, longTasks };
		}, SECONDS * 1000);
		results[`cpu_${rate}x`] = {
			frames: sample.frames.length,
			fps: Math.round((sample.frames.length / SECONDS) * 10) / 10,
			p50_ms: Math.round(percentile(sample.frames, 50) * 10) / 10,
			p95_ms: Math.round(percentile(sample.frames, 95) * 10) / 10,
			long_tasks: sample.longTasks
		};
	}
	stop();
	save('framerate', results);
});
