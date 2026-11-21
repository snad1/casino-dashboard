const DECIMALS = 8;

/**
 * An amount at the casino's 8-decimal precision, trailing zeros dropped down to 2 decimals:
 * 1.01000000 shows as 1.01, 1.00000100 as 1.000001, 0.00000001 stays as is.
 */
export function formatAmount(value: number): string {
	const [whole, fraction = ''] = value.toFixed(DECIMALS).split('.');
	return `${whole}.${fraction.replace(/0+$/, '').padEnd(2, '0')}`;
}

/** Signed profit, e.g. +0.00012 or -1.50. */
export function formatProfit(value: number): string {
	return `${value < 0 ? '-' : '+'}${formatAmount(Math.abs(value))}`;
}
