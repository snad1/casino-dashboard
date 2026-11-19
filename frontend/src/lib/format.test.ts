import { describe, expect, it } from 'vitest';
import { decimalsFor, formatAmount, formatProfit } from './format';

describe('formatProfit', () => {
	it('prefixes gains with a plus sign', () => {
		expect(formatProfit(0.00012)).toBe('+0.00012000');
	});

	it('prefixes losses with a minus sign', () => {
		expect(formatProfit(-1.5, 2)).toBe('-1.50');
	});

	it('treats zero as a gain', () => {
		expect(formatProfit(0, 2)).toBe('+0.00');
	});
});

describe('currency precision', () => {
	it('shows dollar-pegged coins with 4 decimals and others with 8', () => {
		expect(decimalsFor('USDT')).toBe(4);
		expect(decimalsFor('btc')).toBe(8);
		expect(decimalsFor(null)).toBe(8);
		expect(formatAmount(1.5, 'usdc')).toBe('1.5000');
		expect(formatAmount(0.0001, 'doge')).toBe('0.00010000');
	});
});
