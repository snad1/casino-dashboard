import { describe, expect, it } from 'vitest';
import { formatValue } from './status';

describe('formatValue', () => {
	it('shows short times in ms, longer in s or min', () => {
		expect(formatValue(0.0042, 's')).toBe('4 ms');
		expect(formatValue(4, 's')).toBe('4.0 s');
		expect(formatValue(600, 's')).toBe('10 min');
	});

	it('shows counts as whole numbers and unknown values as a dash', () => {
		expect(formatValue(7, 'count')).toBe('7');
		expect(formatValue(null, 's')).toBe('-');
		expect(formatValue(undefined, 'count')).toBe('-');
	});
});
