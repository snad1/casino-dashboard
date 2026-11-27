import { expect, test, type Page } from '@playwright/test';
import { controlMenu, goatedControlMenu, operatorMe, stubApi, WOLFBET } from './stub';

const GOATED = { name: 'goated', label: 'Goated', ctl: 'goatedctl', installed: true, live: true };
const JOB_ID = 'cd'.repeat(16);
const started = {
	status: 202,
	json: { status: 'running', exit_code: null, job_id: JOB_ID, output: null }
};

async function rig(page: Page): Promise<Array<{ path: string; body: unknown }>> {
	const posts: Array<{ path: string; body: unknown }> = [];
	page.on('request', (request) => {
		const path = new URL(request.url()).pathname.replace('/api', '');
		if (request.method() === 'POST' && path.includes('/control/')) {
			posts.push({ path, body: request.postData() ? request.postDataJSON() : null });
		}
	});
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: operatorMe }),
		'GET /services': () => ({ status: 200, json: [WOLFBET, GOATED] }),
		'GET /goated/control': () => ({ status: 200, json: goatedControlMenu }),
		'GET /wolfbet/control': () => ({ status: 200, json: controlMenu }),
		'POST /goated/control/benchmark': () => started,
		[`GET /goated/jobs/${JOB_ID}`]: () => ({
			status: 200,
			json: {
				job_id: JOB_ID,
				cmd: 'goated.benchmark',
				status: 'succeeded',
				exit_code: 0,
				duration_ms: 10,
				lines: 0
			}
		})
	});
	return posts;
}

test("goated's export needs a session, WolfBet's does not", async ({ page }) => {
	const posts = await rig(page);
	await page.goto('/goated/tools/export');
	await expect(page.getByLabel('Session id (needed)')).toBeVisible();
	await page.getByRole('button', { name: 'Export' }).click();
	expect(posts).toEqual([]);
	await page.goto('/wolfbet/tools/export');
	await expect(page.getByLabel('Session id (blank for all)')).toBeVisible();
});

test("goated's tools show only the fields goated takes", async ({ page }) => {
	await rig(page);
	await page.goto('/goated/tools/health');
	await expect(page.getByLabel('Calls')).toBeVisible();
	await expect(page.getByLabel('Lanes (blank for one)')).toHaveCount(0);
	await expect(page.getByText('Each is a 0-amount bet')).toBeVisible();
	await page.goto('/goated/tools/backtest');
	await expect(page.getByLabel('Seed')).toBeVisible();
	for (const name of ['Sessions', 'Runs', 'Balance']) {
		await expect(page.getByLabel(name, { exact: true })).toHaveCount(0);
	}
	await page.goto('/wolfbet/tools/health');
	await expect(page.getByLabel('Lanes (blank for one)')).toBeVisible();
});

test("goated's benchmark asks before it runs a real 0-amount bet", async ({ page }) => {
	const posts = await rig(page);
	await page.goto('/goated/tools/health');
	await page.getByLabel('Calls').fill('5');
	await page.getByRole('button', { name: 'Benchmark' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('Each is a 0-amount bet');
	expect(posts).toEqual([]);
	await dialog.getByRole('button', { name: 'Benchmark' }).click();
	await expect
		.poll(() => posts)
		.toEqual([{ path: '/goated/control/benchmark', body: { count: 5 } }]);
});

test("goated's update has no branch box; WolfBet's keeps it", async ({ page }) => {
	await rig(page);
	await page.goto('/goated/control');
	await expect(page.getByRole('button', { name: 'Update' }).first()).toBeVisible();
	await expect(page.getByLabel('Branch (blank keeps the current one)')).toHaveCount(0);
	await page.goto('/wolfbet/control');
	await expect(page.getByLabel('Branch (blank keeps the current one)')).toBeVisible();
});
