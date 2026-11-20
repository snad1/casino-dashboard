/** A status-page number as people read it: milliseconds under a second, counts as is. */
export function formatValue(value: number | null | undefined, unit: string): string {
	if (value === null || value === undefined) return '-';
	if (unit === 's') {
		if (value < 1) return `${Math.round(value * 1000)} ms`;
		if (value < 120) return `${value.toFixed(1)} s`;
		return `${Math.round(value / 60)} min`;
	}
	return String(Math.round(value));
}
