import { describe, expect, it } from 'vitest';
import {
	addDropSamples,
	droppedLastMinute,
	formatBytes,
	formatPercent,
	ingestWarnings,
	publisherWarnings,
	type DropSamples,
	type ReconcileRun
} from './system';

const counters = (dropped: number) => ({
	enabled: true,
	connected: true,
	sent: 100,
	queued: 0,
	dropped
});

function feed(steps: Array<[number, number]>, lane = 2): DropSamples {
	let samples: DropSamples = {};
	for (const [at, dropped] of steps)
		samples = addDropSamples(samples, { [lane]: counters(dropped) }, at);
	return samples;
}

const run = (overrides: Partial<ReconcileRun> = {}): ReconcileRun => ({
	finished_at: '2026-01-05T10:00:00Z',
	kind: 'quick',
	mismatched: 0,
	persisting: 0,
	purged_upstream: 0,
	lag_rows: 5,
	lag_seconds: 42,
	lag_alert: false,
	...overrides
});

describe('publisher drops', () => {
	it('warns about a rise within the last minute', () => {
		const samples = feed([
			[0, 4],
			[30_000, 7]
		]);
		expect(droppedLastMinute(samples)).toEqual({ 2: 3 });
		expect(publisherWarnings(samples)).toMatchInlineSnapshot(`
			[
			  "Lane 2 publisher dropped 3 events in the last minute.",
			]
		`);
	});

	it('forgets a rise older than a minute', () => {
		const samples = feed([
			[0, 4],
			[1_000, 7],
			[62_000, 7],
			[63_000, 7]
		]);
		expect(samples[2]?.map((s) => s.at)).toEqual([62_000, 63_000]);
		expect(publisherWarnings(samples)).toEqual([]);
	});

	it('treats a lower count as a restart, not a negative drop', () => {
		const samples = feed([
			[0, 9],
			[1_000, 0],
			[2_000, 1]
		]);
		expect(droppedLastMinute(samples)).toEqual({ 2: 1 });
	});
});

describe('ingest warnings', () => {
	it('is quiet for a clean run or no run', () => {
		expect(ingestWarnings(null)).toEqual([]);
		expect(ingestWarnings(run())).toEqual([]);
	});

	it('names lag and persisting differences in words', () => {
		expect(ingestWarnings(run({ lag_alert: true, lag_seconds: 1800, persisting: 2 })))
			.toMatchInlineSnapshot(`
				[
				  "History copy is 30 min behind, close to the bot's purge window.",
				  "2 sessions differ between the bot and the history copy.",
				]
			`);
	});
});

describe('formatters', () => {
	it('reads bytes and percent, and n/a when missing', () => {
		expect([0, 1536, 3 * 1024 ** 3, null].map(formatBytes)).toEqual([
			'0 B',
			'1.5 KB',
			'3.0 GB',
			'n/a'
		]);
		expect([12.345, undefined].map(formatPercent)).toEqual(['12.3%', 'n/a']);
	});
});
