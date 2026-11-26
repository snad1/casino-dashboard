import { beforeEach, describe, expect, it, vi } from 'vitest';

const stored = new Map<string, string>();
vi.stubGlobal('localStorage', {
	getItem: (key: string) => stored.get(key) ?? null,
	setItem: (key: string, value: string) => stored.set(key, value)
});
const reply = { data: [] as unknown[] | undefined };
vi.mock('#lib/session.svelte', () => ({ api: { GET: vi.fn(async () => reply) } }));

const service = (name: string) => ({
	name,
	label: name,
	ctl: `${name}ctl`,
	installed: true,
	live: true
});

async function fresh(list: unknown[] | undefined, pick: string | null = null) {
	stored.clear();
	if (pick !== null) stored.set('casino.service', pick);
	vi.resetModules();
	reply.data = list;
	const { services } = await import('./services.svelte');
	await services.load();
	return services;
}

describe('the picked service', () => {
	beforeEach(() => stored.clear());

	it('is the stored one while the user may still open it', async () => {
		const services = await fresh([service('wolfbet'), service('goated')], 'goated');
		expect(services.picked()).toBe('goated');
	});

	it('falls back to the first service when the stored one is not listed', async () => {
		const services = await fresh([service('wolfbet'), service('goated')], 'stake');
		expect(services.picked()).toBe('wolfbet');
	});

	it('is null with no service, and a failed list is empty', async () => {
		expect((await fresh([])).picked()).toBeNull();
		const failed = await fresh(undefined);
		expect(failed.list).toEqual([]);
	});

	it('keeps a pick only for a listed service', async () => {
		const services = await fresh([service('wolfbet'), service('goated')]);
		services.pick('stake');
		expect(stored.get('casino.service')).toBeUndefined();
		services.pick('goated');
		expect(stored.get('casino.service')).toBe('goated');
		expect(services.picked()).toBe('goated');
	});
});
