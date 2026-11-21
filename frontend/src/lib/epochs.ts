import type { components } from '#lib/api/schema';

export type Epoch = components['schemas']['EpochOut'];

/** The archived bot database the page shows, from `?epoch=`; null means since the last reset. */
export function parseEpoch(params: Pick<URLSearchParams, 'get'>): number | null {
	const value = Number(params.get('epoch'));
	return Number.isInteger(value) && value > 0 ? value : null;
}

const day = (iso: string) =>
	new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

/** "11 Mar 2026 - 21 Nov 2026": first to last session start of that database. */
export function epochLabel(epoch: Epoch): string {
	const from = day(epoch.first_started_at);
	return epoch.last_started_at ? `${from} - ${day(epoch.last_started_at)}` : from;
}
