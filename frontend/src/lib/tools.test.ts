import { describe, expect, it } from 'vitest';
import {
	capLines,
	downloadUrl,
	filterLines,
	parseBacktest,
	parseLogLine,
	parseProgress,
	resultRows,
	stripAnsi
} from './tools';

const lines = [
	'2026-11-19 10:00:00  INFO     Session 12 opened',
	'2026-11-19 10:00:01  WARNING  [L2] Slow api call 900 ms',
	'2026-11-19 10:00:02  ERROR    [L3] Bet failed',
	'-- Boot 4f2a --'
].map((text, i) => parseLogLine(i, text));

describe('logs', () => {
	it('strips terminal codes and keeps tabs and newlines', () => {
		expect(stripAnsi('\x1b[31mred\x1b[0m\tok\r\nnext\x07')).toBe('red\tok\nnext');
	});

	it('reads level and lane, untagged lines being lane 1', () => {
		expect(lines.map((l) => [l.level, l.lane])).toEqual([
			['INFO', 1],
			['WARNING', 2],
			['ERROR', 3],
			[null, null]
		]);
	});

	it('filters by level, lane and text', () => {
		const ids = (f: Parameters<typeof filterLines>[1]) => filterLines(lines, f).map((l) => l.id);
		expect(ids({ level: 'all', lane: 'all', search: '' })).toEqual([0, 1, 2, 3]);
		expect(ids({ level: 'ERROR', lane: 'all', search: '' })).toEqual([2]);
		expect(ids({ level: 'all', lane: 2, search: '' })).toEqual([1]);
		expect(ids({ level: 'all', lane: 'all', search: ' SLOW ' })).toEqual([1]);
	});

	it('keeps only the newest lines', () => {
		expect(capLines([1, 2, 3], [4, 5], 4)).toEqual([2, 3, 4, 5]);
		expect(capLines([1], [2], 4)).toEqual([1, 2]);
	});
});

describe('backtest', () => {
	it('reads progress lines only', () => {
		expect(parseProgress('{"progress": 0.25}')).toBe(0.25);
		expect(parseProgress('{"progress": 3}')).toBe(1);
		expect(parseProgress('Backtesting...')).toBeNull();
		expect(parseProgress('[1]')).toBeNull();
	});

	it('takes the last result line and shows its summary', () => {
		const result = { v: 1, mode: 'session', summary: { bets: 10, profit: 0.5, stop: null } };
		const parsed = parseBacktest(['noise', JSON.stringify(result), 'tail text']);
		expect(parsed).toEqual(result);
		if (!parsed) throw new Error('no result');
		expect(resultRows(parsed)).toEqual([
			['bets', '10'],
			['profit', '0.5'],
			['stop', 'none']
		]);
		expect(resultRows({ v: 1, mode: 'session', profit: 1e-8 })).toContainEqual([
			'profit',
			'0.00000001'
		]);
		expect(parseBacktest(['nothing here'])).toBeNull();
	});
});

it('builds the download address from the api, encoded', () => {
	expect(downloadUrl('wolfbet', 'ab'.repeat(16), 'csv')).toBe(
		`/api/wolfbet/jobs/${'ab'.repeat(16)}/file?format=csv`
	);
});
