import { describe, expect, it } from 'vitest';
import type { ConfigOut } from './config';
import { laneShares, parseArchives, restartPlan } from './lanes';

const config = (base_bet: number, lanes: ConfigOut['lanes'] = {}): ConfigOut => ({
	keys: [],
	lane_count: 3,
	override_lanes: { min: 2, max: 10 },
	split_choices: ['none', 'budget', 'bet', 'both'],
	lane_split: 'none',
	base: { base_bet },
	lanes
});

describe('laneShares', () => {
	it('keeps the full bet and balance without a split', () => {
		expect(laneShares(config(0.0001), 2, 'none', 1)).toEqual([
			{ lane: 1, baseBet: 0.0001, budget: 1, ownBet: false },
			{ lane: 2, baseBet: 0.0001, budget: 1, ownBet: false }
		]);
	});

	it('divides the bet, the budget or both', () => {
		const bet = laneShares(config(0.0003), 3, 'bet', 0.9);
		expect(bet.map((s) => [s.baseBet, s.budget])).toEqual([
			[0.0001, 0.9],
			[0.0001, 0.9],
			[0.0001, 0.9]
		]);
		const both = laneShares(config(0.0002), 2, 'both', 1);
		expect(both.map((s) => [s.baseBet, s.budget])).toEqual([
			[0.0001, 0.5],
			[0.0001, 0.5]
		]);
		expect(laneShares(config(0.0002), 2, 'budget', null)[0]?.budget).toBeNull();
	});

	it('keeps a lane override and a bet that would round to 0', () => {
		const shares = laneShares(config(0.0002, { '2': { base_bet: 0.005 } }), 2, 'bet', 1);
		expect(shares[1]).toEqual({ lane: 2, baseBet: 0.005, budget: 1, ownBet: true });
		expect(laneShares(config(0.00000001), 3, 'bet', 1)[0]?.baseBet).toBe(0.00000001);
		expect(laneShares(config(0.0002), 1, 'both', 1)[0]).toEqual({
			lane: 1,
			baseBet: 0.0002,
			budget: 1,
			ownBet: false
		});
	});
});

describe('restartPlan', () => {
	const live = {
		1: { status: 'running' as const },
		2: { status: 'paused' as const },
		3: { status: 'stopped' as const }
	};

	it('restarts running lanes, starts new ones and stops the rest', () => {
		expect(restartPlan(live, 3, 5)).toEqual({ restart: [1, 2], start: [4, 5], stop: [] });
		expect(restartPlan(live, 3, 1)).toEqual({ restart: [1], start: [], stop: [2, 3] });
		expect(restartPlan(live, 3, 3)).toEqual({ restart: [1, 2], start: [], stop: [] });
	});
});

describe('parseArchives', () => {
	it('reads each archive and the total', () => {
		const text = [
			'  .wolfbet_autobot-20261101-120000.db  168 sessions  2026-10-01T09:00 → 2026-11-01T11:59  4.2M',
			'  .wolfbet_autobot-20261115-080000.db  12 sessions  2026-11-01T12:00 → 2026-11-15T07:59  512K',
			'',
			'  Total: 4.7 MB',
			"  Query any archive directly: sqlite3 <archive> 'SELECT ... FROM sessions'"
		].join('\n');
		const { rows, total } = parseArchives(text);
		expect(rows).toEqual([
			{
				name: '.wolfbet_autobot-20261101-120000.db',
				sessions: '168',
				span: '2026-10-01T09:00 → 2026-11-01T11:59',
				size: '4.2M'
			},
			{
				name: '.wolfbet_autobot-20261115-080000.db',
				sessions: '12',
				span: '2026-11-01T12:00 → 2026-11-15T07:59',
				size: '512K'
			}
		]);
		expect(total).toBe('4.7 MB');
	});

	it('is empty when there are none', () => {
		const none = 'No archives found (~/.wolfbet_autobot-*.db).\nCreate one with: wolfctl reset';
		expect(parseArchives(none)).toEqual({ rows: [], total: null });
	});
});
