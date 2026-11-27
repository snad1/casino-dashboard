import { describe, expect, it } from 'vitest';
import {
	exportUrl,
	filterQuery,
	inputValue,
	presetRange,
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
		const filter = { since: '2026-11-01', until: '2026-11-15', lane: 2, epoch: 4, all: false };
		expect(parseFilter(new URLSearchParams(filterQuery(filter)))).toEqual(filter);
	});

	it('keeps any lane id the api takes, such as a lane added later as 12', () => {
		expect(parseFilter(new URLSearchParams('lane=12')).lane).toBe(12);
	});

	it('drops values the form could not send', () => {
		const params = new URLSearchParams(
			'since=yesterday&until=2026-11-01 10:00 PM&lane=100&epoch=0&range=week'
		);
		const none = { since: null, until: null, lane: null, epoch: null, all: false };
		expect(parseFilter(params)).toEqual(none);
		expect(filterQuery(none)).toEqual({});
	});

	it('asks for all time only when no window is set', () => {
		const every = { since: null, until: null, lane: 2, epoch: null, all: true };
		expect(parseFilter(new URLSearchParams(filterQuery(every)))).toEqual(every);
		expect(filterQuery({ ...every, since: '2026-11-01' })).toEqual({
			since: '2026-11-01',
			lane: '2'
		});
		expect(exportUrl('wolfbet', every, 'csv')).toBe(
			'/api/wolfbet/analytics/export?lane=2&range=all&format=csv'
		);
	});

	it('builds the export link with the filter and format', () => {
		const filter = { since: '2026-11-01', until: null, lane: 3, epoch: 2, all: false };
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

describe('time windows', () => {
	const now = new Date('2026-11-25T15:54:00Z');

	it('reads dates and times from the address', () => {
		const params = new URLSearchParams('since=2026-11-25T13:00&until=2026-11-25T16:00:30');
		expect(parseFilter(params)).toMatchObject({
			since: '2026-11-25T13:00',
			until: '2026-11-25T16:00:30'
		});
		expect(parseFilter(new URLSearchParams('since=2026-11-25T1')).since).toBeNull();
	});

	it('builds the quick windows on the bot clock', () => {
		expect(presetRange('today', 'Europe/Berlin', now)).toEqual({
			since: '2026-11-25T00:00',
			until: null
		});
		expect(presetRange('yesterday', 'Europe/Berlin', now)).toEqual({
			since: '2026-11-24T00:00',
			until: '2026-11-24'
		});
		expect(presetRange('week', 'Europe/Berlin', now)).toEqual({
			since: '2026-11-18T16:54',
			until: null
		});
		expect(presetRange('month', 'UTC', now).since).toBe('2026-10-26T15:54');
		const lateUtc = new Date('2026-11-25T23:30:00Z');
		expect(presetRange('today', 'Europe/Berlin', lateUtc).since).toBe('2026-11-26T00:00');
		expect(presetRange('yesterday', 'UTC', new Date('2026-03-01T08:00:00Z')).until).toBe(
			'2026-02-28'
		);
	});

	it('shows a bare date in the picker as the whole day', () => {
		expect(inputValue(null, false)).toBe('');
		expect(inputValue('2026-11-24', false)).toBe('2026-11-24T00:00');
		expect(inputValue('2026-11-24', true)).toBe('2026-11-24T23:59:59');
		expect(inputValue('2026-11-24T13:00', true)).toBe('2026-11-24T13:00');
	});
});
