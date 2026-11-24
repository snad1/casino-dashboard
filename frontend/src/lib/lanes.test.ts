import { describe, expect, it } from 'vitest';
import type { ConfigOut } from './config';
import {
	configTab,
	LANE_NAME_HELP,
	laneActions,
	laneNameError,
	lanePlan,
	laneShares,
	laneTitle,
	parseArchives
} from './lanes';

const config = (base_bet: number, lanes: ConfigOut['lanes'] = {}): ConfigOut => ({
	keys: [],
	lane_count: 3,
	override_lanes: { min: 2, max: 10 },
	split_choices: ['none', 'budget', 'bet', 'both'],
	lane_split: 'none',
	base: { base_bet },
	lanes,
	pending: {},
	rollout: {}
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

describe('lanePlan', () => {
	const live = {
		1: { status: 'running' as const },
		2: { status: 'paused' as const },
		3: { status: 'stopped' as const },
		7: { status: 'running' as const }
	};

	it('starts only the new lanes and stops only the highest ones', () => {
		expect(lanePlan(live, 4, 6)).toEqual({ start: 2, stop: [] });
		expect(lanePlan(live, 4, 2)).toEqual({ start: 0, stop: [3, 7] });
		expect(lanePlan(live, 4, 4)).toEqual({ start: 0, stop: [] });
	});

	it('names nothing to stop when no lane reports yet', () => {
		expect(lanePlan({}, 3, 1)).toEqual({ start: 0, stop: [] });
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

describe('lane card actions', () => {
	const names = (n: number, status: Parameters<typeof laneActions>[1]) =>
		laneActions(n, status).map((a) => a.label);

	it('offers pause or resume, restart and stop on every lane', () => {
		expect(names(2, 'running')).toEqual(['Pause', 'Restart', 'Stop']);
		expect(names(2, 'paused')).toEqual(['Resume', 'Restart', 'Stop']);
		expect(names(1, 'running')).toEqual(['Pause', 'Restart', 'Stop']);
		expect(names(1, 'events_off')).toEqual(['Pause', 'Resume', 'Stop']);
	});

	it('offers start for a lane that is not betting, lane 1 included', () => {
		for (const status of ['stopped', 'stale', 'down'] as const) {
			expect(names(3, status)).toEqual(['Start']);
			expect(names(1, status)).toEqual(['Start']);
		}
	});

	it('targets its own lane only', () => {
		expect(laneActions(4, 'running')).toEqual([
			{ name: 'pause', body: { target: 4 }, label: 'Pause' },
			{ name: 'lane_restart', body: { lane: 4 }, label: 'Restart' },
			{ name: 'lane_stop', body: { lane: 4 }, label: 'Stop', danger: true }
		]);
	});

	it('opens lane 1 on the base tab', () => {
		expect(configTab(1)).toBe('base');
		expect(configTab(3)).toBe('3');
	});
});

describe('lane names', () => {
	it('titles a lane with its own name only', () => {
		expect(laneTitle(3, 'friend-A')).toBe('Lane 3 · friend-A');
		expect(laneTitle(3, null)).toBe('Lane 3');
		expect(laneTitle(1)).toBe('Lane 1');
	});

	it('takes the names the bot takes', () => {
		for (const name of ['friend-A', 'night run', 'x', '_a', 'a-', 'a'.repeat(32)]) {
			expect(laneNameError(name)).toBeNull();
		}
		for (const name of ['', '-x', ' x', 'x ', 'a;b', '%(asctime)s', 'a'.repeat(33)]) {
			expect(laneNameError(name)).toBe(LANE_NAME_HELP);
		}
	});
});
