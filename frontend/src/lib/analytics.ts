import type { components } from '#lib/api/schema';
import { parseEpoch } from '#lib/epochs';
import { formatAmount } from '#lib/format';

export type AnalyticsOut = components['schemas']['AnalyticsOut'];
export type Report = AnalyticsOut['report'];
export type SessionStats = AnalyticsOut['sessions'];

const LANE_ID_MAX = 99;
/** A date, or a date and time to the minute or second, in the bot's clock. */
const DATE = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$/;

/**
 * The page's filter; it lives in the URL so a range can be shared and reloaded. With no
 * dates and no epoch the api shows today; `all` asks for every session instead.
 */
export interface Filter {
	since: string | null;
	until: string | null;
	lane: number | null;
	epoch: number | null;
	all: boolean;
}

/** Read the filter from a query string, dropping anything the form could not have sent. */
export function parseFilter(params: Pick<URLSearchParams, 'get'>): Filter {
	const date = (key: string) => {
		const value = params.get(key);
		return value && DATE.test(value) ? value : null;
	};
	const lane = Number(params.get('lane'));
	return {
		since: date('since'),
		until: date('until'),
		lane: Number.isInteger(lane) && lane >= 1 && lane <= LANE_ID_MAX ? lane : null,
		epoch: parseEpoch(params),
		all: params.get('range') === 'all'
	};
}

/** The filter as query params for the api and the URL, leaving out what is unset. */
export function filterQuery(filter: Filter): Record<string, string> {
	const out: Record<string, string> = {};
	if (filter.since) out.since = filter.since;
	if (filter.until) out.until = filter.until;
	if (filter.lane !== null) out.lane = String(filter.lane);
	if (filter.epoch !== null) out.epoch = String(filter.epoch);
	if (filter.all && !filter.since && !filter.until && filter.epoch === null) out.range = 'all';
	return out;
}

export type Preset = 'today' | 'yesterday' | 'week' | 'month';

export const PRESETS: { id: Preset; label: string }[] = [
	{ id: 'today', label: 'Today' },
	{ id: 'yesterday', label: 'Yesterday' },
	{ id: 'week', label: 'Last 7 days' },
	{ id: 'month', label: 'Last 30 days' }
];

/** `now` on the clock of `zone` as `YYYY-MM-DD` and `HH:MM`. */
function clock(now: Date, zone: string): { day: string; time: string } {
	const parts = Object.fromEntries(
		new Intl.DateTimeFormat('en-CA', {
			timeZone: zone,
			year: 'numeric',
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit',
			hourCycle: 'h23'
		})
			.formatToParts(now)
			.map((p) => [p.type, p.value])
	);
	return {
		day: `${parts.year}-${parts.month}-${parts.day}`,
		time: `${parts.hour}:${parts.minute}`
	};
}

/** The calendar day before `day` (`YYYY-MM-DD`). */
function dayBefore(day: string): string {
	const at = new Date(`${day}T00:00:00Z`);
	at.setUTCDate(at.getUTCDate() - 1);
	return at.toISOString().slice(0, 10);
}

/**
 * A quick window in the bot's clock (`zone`), as the filter sends it. A bare `until` date
 * counts that whole day, as the bot reads it.
 */
export function presetRange(
	preset: Preset,
	zone: string,
	now: Date = new Date()
): { since: string; until: string | null } {
	const today = clock(now, zone).day;
	if (preset === 'today') return { since: `${today}T00:00`, until: null };
	if (preset === 'yesterday') {
		const day = dayBefore(today);
		return { since: `${day}T00:00`, until: day };
	}
	const days = preset === 'week' ? 7 : 30;
	const start = clock(new Date(now.getTime() - days * 86_400_000), zone);
	return { since: `${start.day}T${start.time}`, until: null };
}

/** A filter bound as a `datetime-local` value: a bare `until` date ends at that day's end. */
export function inputValue(bound: string | null, end: boolean): string {
	if (!bound) return '';
	return bound.length > 10 ? bound : `${bound}T${end ? '23:59:59' : '00:00'}`;
}

/** Where the range export of `service` downloads from, with the page's filter. */
export function exportUrl(service: string, filter: Filter, format: 'csv' | 'json'): string {
	const query = new URLSearchParams({ ...filterQuery(filter), format });
	return `/api/${encodeURIComponent(service)}/analytics/export?${query}`;
}

/** A length of time the way the bot prints it: `1mo 20d 2h 3m 2s`, 30-day months. */
export function formatDuration(seconds: number | null | undefined): string {
	let left = Math.max(0, Math.floor(seconds ?? 0));
	const parts: string[] = [];
	for (const [name, size] of [
		['mo', 30 * 86400],
		['d', 86400],
		['h', 3600],
		['m', 60]
	] as const) {
		const count = Math.floor(left / size);
		left -= count * size;
		if (count || parts.length) parts.push(`${count}${name}`);
	}
	parts.push(`${left}s`);
	return parts.join(' ');
}

/** A count with thousands separators, as the bot prints `1,234`. */
export function formatCount(value: number | null | undefined): string {
	return Math.round(value ?? 0).toLocaleString('en-US');
}

/** Wins over bets as `49.0%`, or `-` when there were no bets. */
export function winRate(wins: number | null | undefined, bets: number | null | undefined): string {
	return bets ? `${(((wins ?? 0) / bets) * 100).toFixed(1)}%` : '-';
}

/** A bot time (`2026-11-05T10:00:00`) as `2026-11-05 10:00:00`. */
export function formatTime(value: string | null | undefined): string {
	return (value ?? '').replace('T', ' ').slice(0, 19);
}

export function hourLabel(hour: number): string {
	return `${String(hour).padStart(2, '0')}:00`;
}

const avg = (value: number) => value.toFixed(1);

/** The bot's `--stats` totals as Overall rows, worded the way the CLI prints them. */
export function sessionStatRows(stats: SessionStats): Array<[string, string]> {
	return [
		['Best streaks', `W+${stats.best_win_streak} L-${stats.best_loss_streak}`],
		['Bets/Session', `Max ${formatCount(stats.max_bets)} Avg ${Math.round(stats.avg_bets)}`],
		['Wins/Session', `Max ${formatCount(stats.max_wins)} Avg ${avg(stats.avg_wins)}`],
		['Losses/Session', `Max ${formatCount(stats.max_losses)} Avg ${avg(stats.avg_losses)}`],
		['Peak balance', stats.peak_balance === null ? 'n/a' : formatAmount(stats.peak_balance)],
		[
			'Speed',
			`${avg(stats.avg_bps)} avg/s ${Math.round(stats.avg_bpm)} avg/m Peak: ${Math.round(stats.peak_bps)}/s ${Math.round(stats.peak_bpm)}/m`
		]
	];
}
