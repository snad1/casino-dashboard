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

/**
 * A number as plain decimal text, never in exponent form: 1e-8 reads 0.00000001. Unlike
 * formatAmount it neither rounds nor pads, so a form value comes back exactly as saved.
 */
export function plainNumber(value: number): string {
	const text = String(value);
	const parts = /^(-?)(\d)(?:\.(\d+))?e([+-]\d+)$/.exec(text);
	if (!parts) return text;
	const [, sign, lead, rest = '', exponent] = parts;
	const digits = `${lead}${rest}`;
	const point = 1 + Number(exponent);
	if (point <= 0) return `${sign}0.${'0'.repeat(-point)}${digits}`;
	if (point >= digits.length) return `${sign}${digits}${'0'.repeat(point - digits.length)}`;
	return `${sign}${digits.slice(0, point)}.${digits.slice(point)}`;
}

/** Any saved value as text, numbers in plain decimal form. */
export function plainText(value: unknown): string {
	return typeof value === 'number' ? plainNumber(value) : String(value);
}
