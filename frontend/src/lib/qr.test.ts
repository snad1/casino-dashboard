import { describe, expect, it } from 'vitest';
import { modulesToPath, qrPath } from './qr';

const URI =
	'otpauth://totp/Casino%20dashboard:admin%40example.test?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Casino%20dashboard';

describe('modulesToPath', () => {
	it('merges each row of dark modules into one rectangle per run', () => {
		const modules = [
			[true, true, false, true],
			[false, false, false, false],
			[false, true, true, true]
		];
		expect(modulesToPath(modules)).toBe('M0 0h2v1h-2zM3 0h1v1h-1zM1 2h3v1h-3z');
	});

	it('draws nothing for an all-light matrix', () => {
		expect(modulesToPath([[false, false]])).toBe('');
	});
});

describe('qrPath', () => {
	it('encodes a setup link at a valid QR size with a 4-module quiet zone', async () => {
		const { size, d } = await qrPath(URI);
		const symbol = size - 8;
		expect(symbol).toBeGreaterThanOrEqual(21);
		expect((symbol - 21) % 4).toBe(0);
		expect(d.startsWith('M4 4h7')).toBe(true);
	});

	it('gives the same code for the same link', async () => {
		expect(await qrPath(URI)).toEqual(await qrPath(URI));
	});
});
