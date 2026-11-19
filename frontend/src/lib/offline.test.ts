import { describe, expect, it } from 'vitest';
import { route } from './offline';

const ORIGIN = 'https://wolfbet.example.test';
const ASSETS = new Set([
	'/_app/immutable/entry/app.Czz.js',
	'/offline.html',
	'/icons/icon-192.png'
]);
const at = (path: string, method = 'GET', mode: RequestMode = 'cors', origin = ORIGIN) =>
	route(new URL(path, origin), method, mode, ASSETS, ORIGIN);

describe('service worker routing', () => {
	it('never handles api or live socket requests', () => {
		for (const path of ['/api', '/api/auth/me', '/api/wolfbet/series?from=x', '/ws?ticket=t']) {
			expect(at(path)).toBe('skip');
			expect(at(path, 'GET', 'navigate')).toBe('skip');
		}
	});

	it('leaves other origins and writes alone', () => {
		expect(at('/_app/immutable/entry/app.Czz.js', 'GET', 'cors', 'https://cdn.example.test')).toBe(
			'skip'
		);
		expect(at('/_app/immutable/entry/app.Czz.js', 'POST')).toBe('skip');
	});

	it('serves build files from cache and pages from the network', () => {
		expect(at('/_app/immutable/entry/app.Czz.js')).toBe('cache');
		expect(at('/icons/icon-192.png')).toBe('cache');
		expect(at('/wolfbet/control', 'GET', 'navigate')).toBe('navigate');
		expect(at('/unknown.json')).toBe('skip');
		expect(at('/apix')).toBe('skip');
	});
});
