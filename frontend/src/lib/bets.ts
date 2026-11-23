const MISSING = '-';

export type BetValues = {
	multiplier?: number | null;
	rule?: string | null;
	bet_value?: number | null;
	result_value?: number | null;
};

export type BetColumn = { label: string; value: (bet: BetValues) => string };

const shown = (value: number | null | undefined, digits: number, suffix = '') =>
	value === null || value === undefined || value === 0
		? MISSING
		: `${value.toFixed(digits)}${suffix}`;

const DICE: BetColumn[] = [
	{ label: 'Target', value: (b) => shown(b.bet_value, 2) },
	{
		label: 'Over / under',
		value: (b) => (b.rule === 'over' ? 'Over' : b.rule === 'under' ? 'Under' : MISSING)
	},
	{
		label: 'Roll',
		value: (b) =>
			b.result_value === null || b.result_value === undefined ? MISSING : b.result_value.toFixed(2)
	},
	{ label: 'Payout', value: (b) => shown(b.multiplier, 4, 'x') }
];

const LIMBO: BetColumn[] = [
	{ label: 'Target', value: (b) => shown(b.multiplier, 2, 'x') },
	{ label: 'Result', value: (b) => shown(b.result_value, 2, 'x') }
];

const OTHER: BetColumn[] = [
	{ label: 'Multiplier', value: (b) => shown(b.multiplier, 2, 'x') },
	{ label: 'Result', value: (b) => shown(b.result_value, 2) }
];

/**
 * The game-specific bet columns of a session: dice shows target, side, roll and payout;
 * limbo shows target and result multipliers. A value the casino did not return (stored as
 * 0 or null) reads as a dash, except a dice roll, where 0.00 is a real outcome.
 * @param game the session's game, e.g. `dice`, `dice-auto`, `limbo-auto`
 */
export function betColumns(game: string | null | undefined): BetColumn[] {
	if (game?.startsWith('dice')) return DICE;
	if (game?.startsWith('limbo')) return LIMBO;
	return OTHER;
}
