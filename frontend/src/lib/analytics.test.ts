import { describe, expect, it } from 'vitest';
import {
	exportUrl,
	filterQuery,
	formatCount,
	formatDuration,
	formatTime,
	hourLabel,
	parseFilter,
	sessionStatRows,
	winRate
} from './analytics';

describe('filter', () => {
	it('round trips through the query string', () => {
		const filter = { since: '2026-11-01', until: '2026-11-15', lane: 2, epoch: 4 };
		expect(parseFilter(new URLSearchParams(filterQuery(filter)))).toEqual(filter);
	});

	it('keeps any lane id the api takes, such as a lane added later as 12', () => {
		expect(parseFilter(new URLSearchParams('lane=12')).lane).toBe(12);
	});

	it('drops values the form could not send', () => {
		const params = new URLSearchParams('since=yesterday&until=2026-11-01T10:00&lane=100&epoch=0');
		const none = { since: null, until: null, lane: null, epoch: null };
		expect(parseFilter(params)).toEqual(none);
		expect(filterQuery(none)).toEqual({});
	});

	it('builds the export link with the filter and format', () => {
		const filter = { since: '2026-11-01', until: null, lane: 3, epoch: 2 };
		expect(exportUrl('wolfbet', filter, 'json')).toBe(
			'/api/wolfbet/analytics/export?since=2026-11-01&lane=3&epoch=2&format=json'
		);
	});
});

describe('formatting like the bot', () => {
	it('prints durations with leading zero units dropped', () => {
		expect(formatDuration(0)).toBe('0s');
		expect(formatDuration(null)).toBe('0s');
		expect(formatDuration(60)).toBe('1m 0s');
		expect(formatDuration(3600)).toBe('1h 0m 0s');
		expect(formatDuration(30 * 86400 + 20 * 86400 + 2 * 3600 + 3 * 60 + 2.9)).toBe(
			'1mo 20d 2h 3m 2s'
		);
	});

	it('prints counts, win rates, times and hours', () => {
		expect(formatCount(1234567)).toBe('1,234,567');
		expect(winRate(49, 100)).toBe('49.0%');
		expect(winRate(0, 0)).toBe('-');
		expect(formatTime('2026-11-05T10:00:00.75')).toBe('2026-11-05 10:00:00');
		expect(formatTime(null)).toBe('');
		expect(hourLabel(7)).toBe('07:00');
	});
});

describe('session stats', () => {
	it("reads like the bot's --stats totals", () => {
		const rows = sessionStatRows({
			best_win_streak: 11,
			best_loss_streak: 12,
			max_bets: 17,
			avg_bets: 3.97,
			max_wins: 12,
			avg_wins: 1.96,
			max_losses: 12,
			avg_losses: 2.01,
			peak_balance: 3.6491073,
			avg_bps: 16.66,
			avg_bpm: 1003.2,
			peak_bps: 23,
			peak_bpm: 64
		});
		expect(Object.fromEntries(rows)).toEqual({
			'Best streaks': 'W+11 L-12',
			'Bets/Session': 'Max 17 Avg 4',
			'Wins/Session': 'Max 12 Avg 2.0',
			'Losses/Session': 'Max 12 Avg 2.0',
			'Peak balance': '3.6491073',
			Speed: '16.7 avg/s 1003 avg/m Peak: 23/s 64/m'
		});
	});

	it('says n/a when no session has a balance', () => {
		const zero = { best_win_streak: 0, best_loss_streak: 0, max_bets: 0, avg_bets: 0 };
		const rows = sessionStatRows({
			...zero,
			max_wins: 0,
			avg_wins: 0,
			max_losses: 0,
			avg_losses: 0,
			peak_balance: null,
			avg_bps: 0,
			avg_bpm: 0,
			peak_bps: 0,
			peak_bpm: 0
		});
		expect(Object.fromEntries(rows)['Peak balance']).toBe('n/a');
	});
});
