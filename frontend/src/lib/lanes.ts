import type { ConfigOut } from '#lib/config';
import type { ActionBody, ActionName } from '#lib/control';
import type { Lanes, LaneStatus } from '#lib/live/protocol';

export type Split = 'none' | 'budget' | 'bet' | 'both';

export interface LaneShare {
	lane: number;
	baseBet: number | null;
	/** What the lane may risk; null when no balance is known yet. */
	budget: number | null;
	/** The lane keeps its own base bet override. */
	ownBet: boolean;
}

const round8 = (value: number) => Math.round(value * 1e8) / 1e8;

/**
 * Each lane's base bet and risk budget after a change to `count` lanes with `split`, by the
 * bot's own rule: the bet split divides the base bet unless a lane overrides it or the share
 * rounds to 0, and the budget split divides the balance.
 */
export function laneShares(
	config: ConfigOut,
	count: number,
	split: Split,
	balance: number | null
): LaneShare[] {
	const base = typeof config.base.base_bet === 'number' ? config.base.base_bet : null;
	const splitBet = count > 1 && (split === 'bet' || split === 'both');
	const splitBudget = count > 1 && (split === 'budget' || split === 'both');
	return Array.from({ length: count }, (_, i) => {
		const lane = i + 1;
		const own = config.lanes[String(lane)]?.base_bet;
		if (typeof own === 'number') {
			return { lane, baseBet: own, budget: shareOf(balance, splitBudget, count), ownBet: true };
		}
		let bet = base;
		if (base !== null && splitBet && round8(base / count) > 0) bet = round8(base / count);
		return { lane, baseBet: bet, budget: shareOf(balance, splitBudget, count), ownBet: false };
	});
}

function shareOf(balance: number | null, split: boolean, count: number): number | null {
	if (balance === null) return null;
	return split ? round8(balance / count) : balance;
}

export interface LanePlan {
	/** How many new lanes start; the bot picks their numbers. */
	start: number;
	/** The lanes that stop: the highest numbers go first. */
	stop: number[];
}

/**
 * What `wolfctl lanes` does to go from `current` to `next` lanes: only the difference starts or
 * stops, and every other lane keeps betting.
 */
export function lanePlan(lanes: Lanes, current: number, next: number): LanePlan {
	const known = Object.keys(lanes)
		.map(Number)
		.sort((a, b) => a - b);
	return {
		start: Math.max(0, next - current),
		stop: known.slice(next, Math.max(next, current))
	};
}

export interface Archive {
	name: string;
	sessions: string;
	span: string;
	size: string;
}

/** The archive rows of `wolfctl archives`, plus its total; empty when it lists none. */
export function parseArchives(text: string): { rows: Archive[]; total: string | null } {
	const rows: Archive[] = [];
	let total: string | null = null;
	for (const line of text.split('\n')) {
		const row = /^\s+(\S+\.db)\s+(\S+) sessions\s+(.+?)\s{2,}(\S+)\s*$/.exec(line);
		if (row) {
			rows.push({
				name: row[1] ?? '',
				sessions: row[2] ?? '',
				span: row[3] ?? '',
				size: row[4] ?? ''
			});
			continue;
		}
		const sum = /^\s+Total:\s+(.+?)\s*$/.exec(line);
		if (sum) total = sum[1] ?? null;
	}
	return { rows, total };
}

export interface LaneAction {
	name: ActionName;
	body: ActionBody;
	label: string;
	danger?: boolean;
}

/** Whether a lane with this live status has a process to stop (else it can only be started). */
export function isUp(status: LaneStatus | undefined): boolean {
	return status === 'running' || status === 'paused' || status === 'events_off';
}

/** The buttons a lane card offers for its live status; every lane, lane 1 included, has its own. */
export function laneActions(n: number, status: LaneStatus): LaneAction[] {
	const pause: LaneAction = { name: 'pause', body: { target: n }, label: 'Pause' };
	const resume: LaneAction = { name: 'resume', body: { target: n }, label: 'Resume' };
	const restart: LaneAction = {
		name: 'lane_restart_end',
		body: { lane: n },
		label: 'Restart at session end'
	};
	const restartNow: LaneAction = {
		name: 'lane_restart',
		body: { lane: n },
		label: 'Restart now',
		danger: true
	};
	const stop: LaneAction = { name: 'lane_stop', body: { lane: n }, label: 'Stop', danger: true };
	const start: LaneAction = { name: 'lane_start', body: { lane: n }, label: 'Start' };
	if (!isUp(status)) return [start];
	switch (status) {
		case 'running':
			return [pause, restart, restartNow, stop];
		case 'paused':
			return [resume, restart, restartNow, stop];
		default:
			return [pause, resume, stop];
	}
}

export const LANE_STATUS_WORDS: Record<LaneStatus, string> = {
	running: 'Running',
	paused: 'Paused',
	stopped: 'Stopped',
	stale: 'Stale',
	down: 'Down',
	events_off: 'Events off'
};

/** `Lane 3 · friend-A` once the lane has its own name, else `Lane 3`. */
export function laneTitle(n: number, name?: string | null): string {
	return name ? `Lane ${n} · ${name}` : `Lane ${n}`;
}

/** `lane 3`, `lanes 3, 4`, or empty for no lane, as a role reads in a sentence. */
export function lanesText(lanes: readonly number[]): string {
	if (!lanes.length) return '';
	return `${lanes.length === 1 ? 'lane' : 'lanes'} ${[...lanes].sort((a, b) => a - b).join(', ')}`;
}

/** `key lakai`, `keys a, b`, or empty for no key, as a role reads in a sentence. */
export function keysText(keys: readonly string[]): string {
	if (!keys.length) return '';
	return `${keys.length === 1 ? 'key' : 'keys'} ${[...keys].sort().join(', ')}`;
}

/** Lane `n` as the live view knows it: with its own name when it has one. */
export function laneLabel(n: number, lanes: Lanes): string {
	return laneTitle(n, lanes[n]?.name);
}

const LANE_NAME = /^[A-Za-z0-9_]([A-Za-z0-9 _-]{0,30}[A-Za-z0-9_-])?$/;
export const LANE_NAME_HELP = '1-32 letters, digits, space, _ or -, not starting with - or a space';

/** Why `name` can't be a lane name, or null when it can: the rule the bot and the API check. */
export function laneNameError(name: string): string | null {
	return LANE_NAME.test(name) ? null : LANE_NAME_HELP;
}

/** The Config page tab that edits lane `n`: lane 1 is the base config. */
export function configTab(n: number): string {
	return n <= 1 ? 'base' : String(n);
}
