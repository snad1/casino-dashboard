/** Signed profit with a fixed number of decimals, e.g. +0.00012000 or -1.50000000. */
export function formatProfit(value: number, decimals = 8): string {
	const fixed = Math.abs(value).toFixed(decimals);
	return `${value < 0 ? '-' : '+'}${fixed}`;
}
