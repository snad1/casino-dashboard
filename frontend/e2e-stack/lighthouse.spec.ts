import { mkdirSync, writeFileSync } from 'node:fs';
import { chromium, expect, test } from '@playwright/test';
import lighthouse from 'lighthouse';
import { signIn } from './stack';

const PORT = 9333;
const PAGES = ['/login', '/wolfbet', '/wolfbet/sessions/1/6'];
const MIN = { accessibility: 0.95, 'best-practices': 0.9 };

test('Lighthouse accessibility and best practices on the signed-in pages', async ({
	isMobile,
	baseURL
}) => {
	test.skip(isMobile, 'Lighthouse runs once, with its own mobile emulation off');
	test.setTimeout(180_000);
	const browser = await chromium.launch({ args: [`--remote-debugging-port=${PORT}`] });
	try {
		const context = await browser.newContext({ ignoreHTTPSErrors: true, baseURL });
		const page = await context.newPage();
		await signIn(page);
		mkdirSync('test-results/lighthouse', { recursive: true });
		const scores: Record<string, Record<string, number>> = {};
		for (const path of PAGES) {
			const result = await lighthouse(`${baseURL}${path}`, {
				port: PORT,
				output: 'json',
				onlyCategories: Object.keys(MIN),
				formFactor: 'desktop',
				screenEmulation: { disabled: true },
				disableStorageReset: true
			});
			if (!result) throw new Error(`Lighthouse returned nothing for ${path}`);
			const name = path.replaceAll('/', '_') || 'root';
			writeFileSync(`test-results/lighthouse/${name}.json`, String(result.report));
			scores[path] = Object.fromEntries(
				Object.keys(MIN).map((key) => [key, result.lhr.categories[key]?.score ?? 0])
			);
		}
		console.log(JSON.stringify(scores, null, 1));
		for (const [path, got] of Object.entries(scores)) {
			for (const [key, min] of Object.entries(MIN)) {
				expect(got[key], `${key} on ${path}`).toBeGreaterThanOrEqual(min);
			}
		}
	} finally {
		await browser.close();
	}
});
