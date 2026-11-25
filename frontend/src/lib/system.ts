import type { components } from '#lib/api/schema';
import { laneLabel } from '#lib/lanes';
import type { Lanes, PublisherCounters } from '#lib/live/protocol';

export const DROP_WINDOW_MS = 60_000;
export type DropSamples = Record<number, Array<{ at: number; dropped: number }>>;
export type ReconcileRun = components['schemas']['ReconcileOut'];

/** Record each lane's dropped count, keeping only the last minute of samples. */
export function addDropSamples(
	samples: DropSamples,
	publisher: Record<number, PublisherCounters>,
	now: number
): DropSamples {
	const next: DropSamples = {};
	for (const [lane, counters] of Object.entries(publisher)) {
		const kept = (samples[Number(lane)] ?? []).filter((s) => now - s.at <= DROP_WINDOW_MS);
		next[Number(lane)] = [...kept, { at: now, dropped: counters.dropped }];
	}
	return next;
}

/**
 * Events each lane's publisher dropped in the last minute. The counter restarts with the bot,
 * so only increases between samples count; a lower value is a restart, not a negative drop.
 */
export function droppedLastMinute(samples: DropSamples): Record<number, number> {
	const out: Record<number, number> = {};
	for (const [lane, series] of Object.entries(samples)) {
		let total = 0;
		for (let i = 1; i < series.length; i += 1) {
			const step = (series[i]?.dropped ?? 0) - (series[i - 1]?.dropped ?? 0);
			if (step > 0) total += step;
		}
		out[Number(lane)] = total;
	}
	return out;
}

export function publisherWarnings(samples: DropSamples, lanes: Lanes = {}): string[] {
	return Object.entries(droppedLastMinute(samples))
		.filter(([, dropped]) => dropped > 0)
		.sort(([a], [b]) => Number(a) - Number(b))
		.map(
			([lane, dropped]) =>
				`${laneLabel(Number(lane), lanes)} publisher dropped ${dropped} events in the last minute.`
		);
}

export function ingestWarnings(run: ReconcileRun | null): string[] {
	if (!run) return [];
	const warnings: string[] = [];
	if (run.lag_alert) {
		warnings.push(
			`History copy is ${Math.round(run.lag_seconds / 60)} min behind, close to the bot's purge window.`
		);
	}
	if (run.persisting > 0) {
		warnings.push(`${run.persisting} sessions differ between the bot and the history copy.`);
	}
	return warnings;
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export function formatBytes(bytes: number | null | undefined): string {
	if (typeof bytes !== 'number') return 'n/a';
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < UNITS.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(unit === 0 ? 0 : 1)} ${UNITS[unit]}`;
}

export function formatPercent(value: number | null | undefined): string {
	return typeof value === 'number' ? `${value.toFixed(1)}%` : 'n/a';
}
