import { expect, test } from '@playwright/test';
import { stubApi, viewerMe } from './stub';

const SNAPSHOT = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: { 1: { status: 'running', profit: 0.5, session_id: 3 } },
	tape: []
};

async function chartRig(page: import('@playwright/test').Page, failSeries = false) {
	const queries: URLSearchParams[] = [];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'one-time', expires_in: 30 } }),
		'GET /wolfbet/series': (_body, url) => {
			queries.push(url.searchParams);
			if (failSeries) return { status: 500, json: { detail: 'boom' } };
			const from = Date.parse(url.searchParams.get('from') ?? '');
			const to = Date.parse(url.searchParams.get('to') ?? '');
			const res = to - from > 86_400_000 ? '1m' : '1s';
			const t = (offset: number) => new Date(to - offset).toISOString();
			return {
				status: 200,
				json: {
					res,
					series: [
						{
							lane: 1,
							points: [
								[t(60_000), 0.5],
								[t(30_000), 0.25]
							]
						}
					]
				}
			};
		},
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } })
	});
	await page.routeWebSocket(/\/ws\?ticket=/, (ws) => {
		ws.onMessage(() => ws.send(JSON.stringify(SNAPSHOT)));
	});
	return queries;
}

const spanSeconds = (q: URLSearchParams) =>
	(Date.parse(q.get('to') ?? '') - Date.parse(q.get('from') ?? '')) / 1000;

test('the chart loads the last hour of profit and switches to balance and 7 days', async ({
	page
}) => {
	const queries = await chartRig(page);
	await page.goto('/wolfbet');
	const chart = page.getByRole('img', { name: /Profit for all lanes, last 1h/ });
	await expect(chart).toHaveAccessibleName(/latest \+0\.75000000/);
	await expect(page.getByTitle('Resolution')).toHaveText('1s');
	expect(queries[0]?.get('metric')).toBe('profit');
	expect(spanSeconds(queries[0] as URLSearchParams)).toBeCloseTo(3600, -1);

	await page.getByRole('button', { name: 'balance' }).click();
	await expect(page.getByRole('img', { name: /Balance for all lanes/ })).toBeVisible();
	expect(queries.at(-1)?.get('metric')).toBe('balance');

	await page.getByRole('button', { name: '7d' }).click();
	await expect(page.getByTitle('Resolution')).toHaveText('1m');
	expect(spanSeconds(queries.at(-1) as URLSearchParams)).toBeCloseTo(7 * 86400, -1);
});

test('a failed history load says so and keeps the chart', async ({ page }) => {
	await chartRig(page, true);
	await page.goto('/wolfbet');
	await expect(page.getByRole('alert')).toHaveText(
		"Couldn't load the chart history. Retrying when you change the range."
	);
	await expect(page.getByRole('img', { name: /Profit for all lanes/ })).toBeVisible();
});
