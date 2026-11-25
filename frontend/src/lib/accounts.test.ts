import { describe, expect, it } from 'vitest';
import { accountOf, accounts, keysOf, lanesOf, MAIN_KEY } from './accounts';

const LANES = {
	1: {
		key_ref: 'default',
		currency: 'usdt',
		balance: 3.65,
		profit: 0.1,
		total_bets: 10,
		status: 'running'
	},
	5: {
		key_ref: 'lakai',
		currency: 'usdt',
		balance: 12.5,
		profit: -0.2,
		total_bets: 4,
		status: 'running'
	},
	6: {
		key_ref: 'lakai',
		currency: 'usdt',
		balance: 12.4,
		profit: 0.05,
		total_bets: 2,
		status: 'down'
	},
	7: { currency: 'usdt', balance: 3.64, profit: 0.3, total_bets: 6, status: 'paused' }
} as const;

describe('accounts', () => {
	it('reads a lane with no key as the main key', () => {
		expect(accountOf(LANES[7])).toBe(MAIN_KEY);
		expect(keysOf(LANES)).toEqual(['default', 'lakai']);
	});

	it('keeps only the lanes of one key, or all of them', () => {
		expect(Object.keys(lanesOf(LANES, 'lakai'))).toEqual(['5', '6']);
		expect(lanesOf(LANES, null)).toBe(LANES);
		expect(lanesOf(LANES, 'nobody')).toEqual({});
	});

	it('gives each account its own balance and profit, never added across accounts', () => {
		const out = accounts(LANES);
		expect(out.map((a) => [a.key, a.currency, a.balance, a.lanes, a.running])).toEqual([
			['default', 'usdt', 3.65, [1, 7], 2],
			['lakai', 'usdt', 12.5, [5, 6], 1]
		]);
		expect(out[0]?.profit).toBeCloseTo(0.4);
		expect(out[1]?.profit).toBeCloseTo(-0.15);
		expect(out[1]?.bets).toBe(6);
	});

	it('splits one key betting in two currencies', () => {
		const out = accounts({
			1: { currency: 'btc', balance: 1 },
			2: { currency: 'usdt', balance: 5 }
		});
		expect(out.map((a) => a.currency)).toEqual(['btc', 'usdt']);
	});
});
