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

	it('never returns to the sign-in page, but follows its own next', () => {
		expect(safeNext('/login?next=%2Fwolfbet%2Fcontrol')).toBe('/wolfbet/control');
		expect(safeNext('/login?next=%2Flogin%3Fnext%3D%252Fadmin%252Fusers')).toBe('/admin/users');
		expect(safeNext('/login')).toBe('/');
		expect(safeNext('/login?next=%2F%2Fevil.test')).toBe('/');
		expect(safeNext('/login?next=%E0%A4%A')).toBe('/');
		expect(loginUrl('/login?next=%2Fwolfbet')).toBe('/login?next=%2Fwolfbet');
		let deep = '/wolfbet/control';
		for (let i = 0; i < 20; i++) deep = `/login?next=${encodeURIComponent(deep)}`;
		expect(safeNext(deep)).toBe('/');
	});

	it('never sends the user to another site', () => {
		for (const next of ['https://evil.test', '//evil.test', '/\\evil.test', null, '']) {
			expect(safeNext(next)).toBe('/');
		}
	});
});
