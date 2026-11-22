import { describe, expect, it } from 'vitest';
import { formatAmount, formatProfit, plainNumber } from './format';

describe('formatAmount', () => {
	it('drops trailing zeros but keeps two decimals', () => {
		expect(formatAmount(1.01)).toBe('1.01');
		expect(formatAmount(1.000001)).toBe('1.000001');
		expect(formatAmount(1)).toBe('1.00');
		expect(formatAmount(0)).toBe('0.00');
	});

	it('keeps the smallest unit of 0.00000001', () => {
		expect(formatAmount(0.00000001)).toBe('0.00000001');
		expect(formatAmount(12.12345678)).toBe('12.12345678');
	});

	it('rounds anything finer than 8 decimals', () => {
		expect(formatAmount(0.000000004)).toBe('0.00');
		expect(formatAmount(0.123456789)).toBe('0.12345679');
	});
});

describe('formatProfit', () => {
	it('prefixes gains with a plus sign', () => {
		expect(formatProfit(0.00012)).toBe('+0.00012');
		expect(formatProfit(1.01)).toBe('+1.01');
	});

	it('prefixes losses with a minus sign', () => {
		expect(formatProfit(-1.5)).toBe('-1.50');
		expect(formatProfit(-0.00000001)).toBe('-0.00000001');
	});

	it('treats zero as a gain', () => {
		expect(formatProfit(0)).toBe('+0.00');
	});
});

describe('plainNumber', () => {
	it('never writes an exponent and keeps every digit', () => {
		expect(plainNumber(1e-8)).toBe('0.00000001');
		expect(plainNumber(1.5e-7)).toBe('0.00000015');
		expect(plainNumber(-2e-8)).toBe('-0.00000002');
		expect(plainNumber(1e21)).toBe('1000000000000000000000');
		expect(plainNumber(0.1)).toBe('0.1');
		expect(plainNumber(123)).toBe('123');
		expect(plainNumber(2.01)).toBe('2.01');
	});
});
