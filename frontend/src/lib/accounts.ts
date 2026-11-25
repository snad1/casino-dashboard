import type { LaneFields, Lanes } from '#lib/live/protocol';

/** The main key's vault id; a lane that names no key bets with it. */
export const MAIN_KEY = 'default';

/** One casino account as the live lanes show it: a key and the currency it bets in. */
export interface Account {
	key: string;
	currency: string;
	balance: number | null;
	profit: number;
	bets: number;
	wins: number;
	losses: number;
	wagered: number;
	running: number;
	lanes: number[];
}

export function accountOf(lane: LaneFields): string {
	return lane.key_ref || MAIN_KEY;
}

/** The keys the lanes bet with, the main key first, then by name. */
export function keysOf(lanes: Lanes): string[] {
	const keys = new Set(Object.values(lanes).map(accountOf));
	return [...keys].sort(keyOrder);
}

/** Only the lanes betting with `key`; every lane when `key` is null (all keys). */
export function lanesOf(lanes: Lanes, key: string | null): Lanes {
	if (key === null) return lanes;
	return Object.fromEntries(Object.entries(lanes).filter(([, lane]) => accountOf(lane) === key));
}

/**
 * Each account's own totals, never added across accounts: lanes on one key and currency
 * share the casino balance, so the account shows one balance (the highest any of its lanes
 * last read, as they move together) and the profit and bets of its lanes.
 */
export function accounts(lanes: Lanes): Account[] {
	const out = new Map<string, Account>();
	for (const [n, lane] of Object.entries(lanes).sort(([a], [b]) => Number(a) - Number(b))) {
		const key = accountOf(lane);
		const currency = (lane.currency ?? '').toLowerCase();
		const id = `${key}|${currency}`;
		const account = out.get(id) ?? {
			key,
			currency,
			balance: null,
			profit: 0,
			bets: 0,
			wins: 0,
			losses: 0,
			wagered: 0,
			running: 0,
			lanes: []
		};
		if (typeof lane.balance === 'number') {
			account.balance = Math.max(account.balance ?? lane.balance, lane.balance);
		}
		account.profit += lane.profit ?? 0;
		account.bets += lane.total_bets ?? 0;
		account.wins += lane.wins ?? 0;
		account.losses += lane.losses ?? 0;
		account.wagered += lane.wagered ?? 0;
		if (lane.status === 'running' || lane.status === 'paused') account.running += 1;
		account.lanes.push(Number(n));
		out.set(id, account);
	}
	return [...out.values()].sort(
		(a, b) => keyOrder(a.key, b.key) || a.currency.localeCompare(b.currency)
	);
}

/** The main key first, then the others by name. */
function keyOrder(a: string, b: string): number {
	if (a === b) return 0;
	if (a === MAIN_KEY) return -1;
	if (b === MAIN_KEY) return 1;
	return a.localeCompare(b);
}
