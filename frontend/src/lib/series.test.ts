import { describe, expect, it } from 'vitest';
import type { Candle } from '#lib/live/protocol';
import { LiveFolder, capPoints, mergePoints, needsReload, toPoints } from './series';

const T0 = Date.UTC(2026, 0, 5, 10) / 1000;
const iso = (s: number) => new Date(s * 1000).toISOString();

describe('toPoints', () => {
	const series = [
		{ lane: 2, points: [[iso(T0), 2] as [string, number | null]] },
		{
			lane: 1,
			points: [
				[iso(T0 + 1), -0.5],
				[iso(T0), 1],
				[iso(T0 + 2), null]
			] as [string, number | null][]
		}
	];

	it('turns per-bucket profit into a running total, summing lanes', () => {
		expect(toPoints(series, 'profit', 'all')).toEqual([
			{ time: T0, value: 3 },
			{ time: T0 + 1, value: 2.5 }
		]);
		expect(toPoints(series, 'profit', 1)).toEqual([
			{ time: T0, value: 1 },
			{ time: T0 + 1, value: 0.5 }
		]);
	});

	it('keeps one balance per time', () => {
		expect(toPoints(series, 'balance', 'all')).toEqual([
			{ time: T0, value: 2 },
			{ time: T0 + 1, value: -0.5 }
		]);
	});
});

describe('mergePoints', () => {
	it('is strictly increasing, sorts out-of-order input, lets live win and keeps gaps', () => {
		const history = [
			{ time: 30, value: 3 },
			{ time: 10, value: 1 },
			{ time: 20, value: 2 }
		];
		const live = [
			{ time: 30, value: 3.5 },
			{ time: 90, value: 4 }
		];
		const merged = mergePoints(history, live);
		expect(merged).toEqual([
			{ time: 10, value: 1 },
			{ time: 20, value: 2 },
			{ time: 30, value: 3.5 },
			{ time: 90, value: 4 }
		]);
		const times = merged.map((p) => p.time);
		expect(times).toEqual([...new Set(times)].sort((a, b) => a - b));
	});

	it('caps to the newest points', () => {
		const points = Array.from({ length: 5 }, (_, i) => ({ time: i, value: i }));
		expect(capPoints(points, 2)).toEqual([
			{ time: 3, value: 3 },
			{ time: 4, value: 4 }
		]);
	});
});

describe('LiveFolder', () => {
	const candle = (lane: number, t: number, profit: number[], balance = [100, 100, 100, 100]) =>
		({ lane, t, profit, balance }) as Candle;

	it('continues profit from history and buckets by resolution', () => {
		const folder = new LiveFolder('profit', 'all', '1m', { time: T0, value: 10 });
		expect(folder.add(candle(1, T0 + 5, [1, 1, 1, 1.5]), 7)).toEqual({ time: T0, value: 10.5 });
		expect(folder.add(candle(1, T0 + 6, [1.5, 2, 1.5, 2]), 7)).toEqual({ time: T0, value: 11 });
		expect(folder.add(candle(1, T0 + 61, [2, 2, 1, 1]), 7)).toEqual({ time: T0 + 60, value: 10 });
	});

	it('treats a new session as all new profit and ignores other lanes when filtered', () => {
		const folder = new LiveFolder('profit', 1, '1s', null);
		folder.add(candle(1, T0, [5, 5, 5, 5]), 1);
		expect(folder.add(candle(1, T0 + 1, [0.2, 0.2, 0.2, 0.2]), 2)).toEqual({
			time: T0 + 1,
			value: 0.2
		});
		expect(folder.add(candle(2, T0 + 2, [1, 1, 1, 9]), 2)).toBeNull();
	});

	it('uses the close for balance and drops candles older than the history', () => {
		const folder = new LiveFolder('balance', 'all', '1s', { time: T0 + 10, value: 50 });
		expect(folder.add(candle(1, T0 + 5, [0, 0, 0, 0], [1, 1, 1, 1]), 1)).toBeNull();
		expect(folder.add(candle(1, T0 + 11, [0, 0, 0, 0], [60, 61, 59, 60.5]), 1)).toEqual({
			time: T0 + 11,
			value: 60.5
		});
	});
});

describe('needsReload', () => {
	const loaded = { from: 1000, to: 4600 };
	it('reloads when the view leaves the history or zooms in a lot', () => {
		expect(needsReload(null, loaded)).toBe(true);
		expect(needsReload(loaded, { from: 500, to: 4600 })).toBe(true);
		expect(needsReload(loaded, { from: 3000, to: 4600 })).toBe(true);
		expect(needsReload(loaded, { from: 1200, to: 4700 })).toBe(false);
	});
});
