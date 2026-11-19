import type { TapeRow } from '#lib/live/protocol';

export const TAPE_MAX = 2000;

const key = (row: TapeRow) => `${row.lane}:${row.seq}:${row.ts_ms}`;

/**
 * Add rows that arrived oldest first to a tape kept newest first, dropping rows already on
 * it and keeping at most `max`, so a resent row never shows twice and memory stays bounded.
 */
export function prependRows(tape: TapeRow[], rows: TapeRow[], max = TAPE_MAX): TapeRow[] {
	const seen = new Set(tape.map(key));
	const fresh: TapeRow[] = [];
	for (let i = rows.length - 1; i >= 0; i -= 1) {
		const row = rows[i] as TapeRow;
		if (seen.has(key(row))) continue;
		seen.add(key(row));
		fresh.push(row);
	}
	return fresh.length ? [...fresh, ...tape].slice(0, max) : tape;
}

export interface StreakBucket {
	length: number;
	wins: number;
	losses: number;
}

/** How many win and loss runs of each length the tape holds (newest first in, by length out). */
export function streakHistogram(tape: TapeRow[], lane: number | 'all'): StreakBucket[] {
	const buckets = new Map<number, StreakBucket>();
	const runs = new Map<number, { win: boolean; length: number }>();
	const close = (run: { win: boolean; length: number } | undefined) => {
		if (!run) return;
		const bucket = buckets.get(run.length) ?? { length: run.length, wins: 0, losses: 0 };
		if (run.win) bucket.wins += 1;
		else bucket.losses += 1;
		buckets.set(run.length, bucket);
	};
	for (let i = tape.length - 1; i >= 0; i -= 1) {
		const row = tape[i] as TapeRow;
		if (lane !== 'all' && row.lane !== lane) continue;
		const win = row.state === 'win';
		const run = runs.get(row.lane);
		if (run && run.win === win) run.length += 1;
		else {
			close(run);
			runs.set(row.lane, { win, length: 1 });
		}
	}
	for (const run of runs.values()) close(run);
	return [...buckets.values()].sort((a, b) => a.length - b.length);
}
