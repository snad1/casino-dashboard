import type { ConfigOut } from '#lib/config';
import type { Lanes } from '#lib/live/protocol';

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

export interface RestartPlan {
	restart: number[];
	start: number[];
	stop: number[];
}

/**
 * What `wolfctl lanes` does to go from `current` to `next` lanes: every running lane
 * restarts (its open session is saved and ended), new lanes start, lanes above `next` stop.
 */
export function restartPlan(lanes: Lanes, current: number, next: number): RestartPlan {
	const running = Object.entries(lanes)
		.filter(([, f]) => f.status === 'running' || f.status === 'paused')
		.map(([lane]) => Number(lane))
		.sort((a, b) => a - b);
	const range = (from: number, to: number) =>
		Array.from({ length: Math.max(0, to - from + 1) }, (_, i) => from + i);
	return {
		restart: running.filter((lane) => lane <= next),
		start: range(current + 1, next),
		stop: range(next + 1, current)
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
