import { describe, expect, it } from 'vitest';
import { betColumns, type BetValues } from './bets';

const cells = (game: string | null, bet: BetValues) =>
	betColumns(game).map((c) => [c.label, c.value(bet)]);

describe('betColumns', () => {
	it('shows dice target, side, roll and payout', () => {
		expect(
			cells('dice-auto', { bet_value: 50.49, rule: 'over', result_value: 73.1, multiplier: 2 })
		).toEqual([
			['Target', '50.49'],
			['Over / under', 'Over'],
			['Roll', '73.10'],
			['Payout', '2.0000x']
		]);
	});

	it('keeps a dice roll of zero but dashes a missing target', () => {
		expect(cells('dice', { bet_value: 0, rule: 'under', result_value: 0 })).toEqual([
			['Target', '-'],
			['Over / under', 'Under'],
			['Roll', '0.00'],
			['Payout', '-']
		]);
	});

	it('shows limbo target and result multipliers, never a dice side', () => {
		expect(cells('limbo', { multiplier: 2, result_value: 10.5, rule: 'over' })).toEqual([
			['Target', '2.00x'],
			['Result', '10.50x']
		]);
	});

	it('dashes a limbo result the casino did not return', () => {
		expect(cells('limbo-auto', { multiplier: 2, result_value: 0 })).toEqual([
			['Target', '2.00x'],
			['Result', '-']
		]);
	});

	it('falls back to multiplier and result for an unknown game', () => {
		expect(cells(null, { multiplier: 1.5, result_value: 3 })).toEqual([
			['Multiplier', '1.50x'],
			['Result', '3.00']
		]);
	});
});
