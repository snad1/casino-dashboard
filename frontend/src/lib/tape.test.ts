import { describe, expect, it } from 'vitest';
import type { TapeRow } from '#lib/live/protocol';
import { prependRows, streakHistogram } from './tape';

const row = (lane: number, seq: number, state = 'win'): TapeRow => ({
	lane,
	seq,
	ts_ms: seq * 10,
	amount: 1,
	multiplier: 2,
	result: 60,
	state,
	profit: state === 'win' ? 1 : -1,
	balance: 100
});

describe('prependRows', () => {
	it('puts the newest first, drops repeats and caps the tape', () => {
		const tape = prependRows([], [row(1, 1), row(1, 2)]);
		expect(tape.map((r) => r.seq)).toEqual([2, 1]);
		const next = prependRows(tape, [row(1, 2), row(1, 3), row(1, 4)], 3);
		expect(next.map((r) => r.seq)).toEqual([4, 3, 2]);
		expect(prependRows(next, [row(1, 4)], 3)).toBe(next);
	});
});

describe('streakHistogram', () => {
	it('counts runs per lane from oldest to newest', () => {
		const oldestFirst = [
			row(1, 1, 'win'),
			row(2, 1, 'loss'),
			row(1, 2, 'win'),
			row(1, 3, 'loss'),
			row(2, 2, 'loss'),
			row(1, 4, 'win')
		];
		const tape = prependRows([], oldestFirst);
		expect(streakHistogram(tape, 'all')).toEqual([
			{ length: 1, wins: 1, losses: 1 },
			{ length: 2, wins: 1, losses: 1 }
		]);
		expect(streakHistogram(tape, 2)).toEqual([{ length: 2, wins: 0, losses: 1 }]);
		expect(streakHistogram([], 'all')).toEqual([]);
	});
});
