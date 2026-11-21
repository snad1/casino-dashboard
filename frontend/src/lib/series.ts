import type { components } from '#lib/api/schema';
import type { Candle } from '#lib/live/protocol';

export type Metric = 'profit' | 'balance';
export type Resolution = components['schemas']['SeriesOut']['res'];
export type LaneSel = number | 'all';
export type Preset = '1h' | '24h' | '7d' | '30d';
export interface Point {
	time: number;
	value: number;
}
export interface Range {
	from: number;
	to: number;
}

export const RES_SECONDS: Record<Resolution, number> = {
	'1s': 1,
	'1m': 60,
	'1h': 3600,
	'1d': 86400
};
export const PRESET_SECONDS: Record<Preset, number> = {
	'1h': 3600,
	'24h': 86400,
	'7d': 7 * 86400,
	'30d': 30 * 86400
};
export const MAX_POINTS = 10_000;

export function rangeFor(preset: Preset, now: number): Range {
	return { from: now - PRESET_SECONDS[preset], to: now };
}

/**
 * History from `/series` as chart points, in epoch seconds. Profit arrives as a sum per bucket
 * and is turned into a running total from the start of the range; across all lanes the bucket
 * sums add up. Balance is one account shared by every lane, so any lane's bucket balance
 * stands for it (the highest lane number wins a tie).
 */
export function toPoints(
	series: ReadonlyArray<{
		lane: number;
		points: ReadonlyArray<ReadonlyArray<string | number | null>>;
	}>,
	metric: Metric,
	lane: LaneSel
): Point[] {
	const byTime = new Map<number, number>();
	for (const one of [...series].sort((a, b) => a.lane - b.lane)) {
		if (lane !== 'all' && one.lane !== lane) continue;
		for (const [iso, value] of one.points) {
			if (typeof iso !== 'string' || typeof value !== 'number') continue;
			const time = Math.floor(Date.parse(iso) / 1000);
			byTime.set(time, metric === 'profit' ? (byTime.get(time) ?? 0) + value : value);
		}
	}
	const points = [...byTime.entries()]
		.sort(([a], [b]) => a - b)
		.map(([time, value]) => ({ time, value }));
	if (metric === 'profit') {
		let total = 0;
		for (const point of points) {
			total += point.value;
			point.value = total;
		}
	}
	return points;
}

/** One strictly increasing series: input order does not matter, and the later list wins a tie. */
export function mergePoints(history: Point[], live: Point[]): Point[] {
	const byTime = new Map<number, number>();
	for (const point of [...history, ...live]) byTime.set(point.time, point.value);
	return [...byTime.entries()].sort(([a], [b]) => a - b).map(([time, value]) => ({ time, value }));
}

export function capPoints(points: Point[], max = MAX_POINTS): Point[] {
	return points.length > max ? points.slice(points.length - max) : points;
}

/** Whether the visible range needs fresh history: it left the loaded range, or zoomed in 2x. */
export function needsReload(loaded: Range | null, visible: Range): boolean {
	if (loaded === null) return true;
	if (visible.from < loaded.from) return true;
	const loadedSpan = loaded.to - loaded.from;
	const visibleSpan = visible.to - visible.from;
	return visibleSpan > 0 && visibleSpan * 2 <= loadedSpan;
}

/**
 * The time span the chart shows. The chart's own visible time range stops at the first point,
 * so zooming out past it would never ask for older history; a logical range that starts before
 * the first bar is turned back into time from that bar and the resolution step.
 */
export function zoomedRange(
	logicalFrom: number,
	firstTime: number | null,
	step: number,
	visible: Range,
	now: number
): Range {
	const from =
		firstTime !== null && logicalFrom < 0
			? firstTime + Math.floor(logicalFrom) * step
			: visible.from;
	return { from, to: Math.max(visible.to, now) };
}

/**
 * Folds the live 1 s candles into points at the chart's resolution, continuing from the last
 * history value. Candles carry the lane's session profit, so profit grows by the change in the
 * close; a new session starts from zero, so its first close is all new profit.
 */
export class LiveFolder {
	#closes = new Map<number, { session: number | null; close: number }>();
	#value: number;
	#lastTime: number;

	constructor(
		readonly metric: Metric,
		readonly lane: LaneSel,
		readonly res: Resolution,
		last: Point | null
	) {
		this.#value = last?.value ?? 0;
		this.#lastTime = last?.time ?? 0;
	}

	add(candle: Candle, session: number | null): Point | null {
		if (this.lane !== 'all' && candle.lane !== this.lane) return null;
		const open = candle[this.metric][0];
		const close = candle[this.metric][3];
		if (open === undefined || close === undefined) return null;
		const time = Math.floor(candle.t / RES_SECONDS[this.res]) * RES_SECONDS[this.res];
		if (time < this.#lastTime) return null;
		if (this.metric === 'balance') {
			this.#value = close;
		} else {
			const seen = this.#closes.get(candle.lane);
			if (seen === undefined) this.#value += close - open;
			else if (seen.session !== session) this.#value += close;
			else this.#value += close - seen.close;
			this.#closes.set(candle.lane, { session, close });
		}
		this.#lastTime = time;
		return { time, value: this.#value };
	}
}
