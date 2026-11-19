const STABLE = new Set(['usdt', 'usdc', 'busd', 'dai']);

/** Decimals to show for an amount in `currency`: dollar-pegged coins 4, everything else 8. */
export function decimalsFor(currency: string | null | undefined): number {
	return currency && STABLE.has(currency.toLowerCase()) ? 4 : 8;
}

/** Signed profit with a fixed number of decimals, e.g. +0.00012000 or -1.50000000. */
export function formatProfit(value: number, decimals = 8): string {
	const fixed = Math.abs(value).toFixed(decimals);
	return `${value < 0 ? '-' : '+'}${fixed}`;
}

/** An unsigned amount in the currency's precision. */
export function formatAmount(value: number, currency: string | null | undefined): string {
	return value.toFixed(decimalsFor(currency));
}
