export type QrPath = { size: number; d: string };

/** One SVG path covering the dark modules, each row's runs merged into one rectangle. */
export function modulesToPath(modules: boolean[][]): string {
	const parts: string[] = [];
	modules.forEach((row, y) => {
		let x = 0;
		while (x < row.length) {
			if (!row[x]) {
				x++;
				continue;
			}
			const start = x;
			while (x < row.length && row[x]) x++;
			const run = x - start;
			parts.push(`M${start} ${y}h${run}v1h-${run}z`);
		}
	});
	return parts.join('');
}

/**
 * Encode `text` as a QR code in the browser, with the standard 4-module quiet zone.
 * The encoder loads on demand so it stays out of every page's first load.
 */
export async function qrPath(text: string): Promise<QrPath> {
	const { encode } = await import('uqr');
	const { size, data } = encode(text, { ecc: 'M', border: 4 });
	return { size, d: modulesToPath(data) };
}
