import { describe, expect, it } from 'vitest';
import { formatProfit } from './format';

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
