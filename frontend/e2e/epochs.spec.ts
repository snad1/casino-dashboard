import { expect, test } from '@playwright/test';
import { stubApi, viewerMe } from './stub';

const ARCHIVED = 1;
const SESSION = {
	epoch_id: ARCHIVED,
	id: 7,
	lane: 1,
	started_at: '2026-11-20T10:00:00Z',
	ended_at: '2026-11-20T11:00:00Z',
	currency: 'btc',
	strategy: 'martingale',
	total_bets: 120,
	wins: 60,
	losses: 60,
	profit: 0.25,
	wagered: 12,
	start_balance: 100,
	end_balance: 100.25,
	max_win_streak: 4,
	max_loss_streak: 5
};

test('after a reset the dashboard starts empty, and a previous reset can be picked', async ({
	page
}) => {
	const asked: Array<string | null> = [];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 503, json: { detail: 'x' } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/epochs': () => ({
			status: 200,
			json: {
				items: [
					{
						epoch_id: ARCHIVED,
						first_started_at: '2026-03-11T01:41:25Z',
						last_started_at: '2026-11-20T10:00:00Z',
						current: false
					}
				]
			}
		}),
		'GET /wolfbet/sessions': (_body, url) => {
			const epoch = url.searchParams.get('epoch');
			asked.push(epoch);
			const items = epoch === String(ARCHIVED) ? [SESSION] : [];
			return { status: 200, json: { items, next_cursor: null } };
		}
	});
	await page.goto('/wolfbet');
	const sessions = page.getByRole('region', { name: 'Sessions' });
	await expect(sessions.getByText('No sessions since the last reset.')).toBeVisible();

	const picker = page.getByLabel('Previous resets');
	await expect(picker.getByRole('option')).toHaveText([/Since last reset/, /2026 - .*2026/]);
	await picker.selectOption(String(ARCHIVED));
	await expect(page).toHaveURL(`/wolfbet?epoch=${ARCHIVED}`);
	await expect(sessions.getByRole('link', { name: '#7 · Lane 1' })).toBeVisible();
	await expect(page.getByText('Sessions and chart show a previous reset.')).toBeVisible();
	expect(asked).toContain(String(ARCHIVED));

	await picker.selectOption('');
	await expect(page).toHaveURL('/wolfbet');
	await expect(sessions.getByText('No sessions since the last reset.')).toBeVisible();
});
