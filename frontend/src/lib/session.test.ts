import { describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/paths', () => ({
	resolve: (id: string) => (id === '/(public)/login' ? '/login' : '/')
}));

const { loginUrl, safeNext } = await import('./session.svelte');

describe('return path after sign-in', () => {
	it('keeps a local path', () => {
		expect(safeNext('/wolfbet/control')).toBe('/wolfbet/control');
		expect(loginUrl('/admin/users?x=1')).toBe('/login?next=%2Fadmin%2Fusers%3Fx%3D1');
	});

	it('never sends the user to another site', () => {
		for (const next of ['https://evil.test', '//evil.test', '/\\evil.test', null, '']) {
			expect(safeNext(next)).toBe('/wolfbet');
		}
	});
});
