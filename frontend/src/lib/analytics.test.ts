import { describe, expect, it } from 'vitest';
import {
	exportUrl,
	filterQuery,
	formatCount,
	formatDuration,
	formatTime,
	hourLabel,
	parseFilter,
	winRate
} from './analytics';

describe('filter', () => {
	it('round trips through the query string', () => {
		const filter = { since: '2026-11-01', until: '2026-11-15', lane: 2, epoch: 4 };
		expect(parseFilter(new URLSearchParams(filterQuery(filter)))).toEqual(filter);
	});

	it('drops values the form could not send', () => {
		const params = new URLSearchParams('since=yesterday&until=2026-11-01T10:00&lane=11&epoch=0');
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
