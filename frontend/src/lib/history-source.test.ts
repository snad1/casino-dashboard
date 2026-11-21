import { beforeEach, describe, expect, it, vi } from 'vitest';

const calls: Array<{ path: string; options: unknown }> = [];
vi.mock('$app/paths', () => ({
	resolve: (id: string, params: Record<string, string>) =>
		Object.entries(params).reduce(
			(out, [k, v]) => out.replace(new RegExp(`\\[${k}(=[a-z]+)?\\]`), v),
			id.replace('/(app)', '')
		)
}));
vi.mock('./session.svelte', () => ({
	api: {
		GET: (path: string, options: unknown) => {
			calls.push({ path, options });
			return Promise.resolve({ data: { items: [], next_cursor: null }, response: { ok: true } });
		}
	}
}));

const { serviceSource, tgSource } = await import('./history-source');

beforeEach(() => {
	calls.length = 0;
});

describe('history sources', () => {
	it('reads a service with its lane filter', async () => {
		const source = serviceSource('wolfbet');
		await source.sessions({ limit: 50, lane: 2 });
		expect(calls[0]).toEqual({
			path: '/{service}/sessions',
			options: { params: { path: { service: 'wolfbet' }, query: { limit: 50, lane: 2 } } }
		});
		expect(source.sessionHref(7, 3)).toBe('/wolfbet/sessions/7/3');
		expect(source.live).toBe(true);
	});

	it('reads a database a reset archived, without live ticks', async () => {
		const source = serviceSource('wolfbet', 1);
		await source.sessions({ limit: 50 });
		await source.series({ metric: 'profit', from: 'a', to: 'b' });
		expect(calls.map((c) => c.options)).toEqual([
			{ params: { path: { service: 'wolfbet' }, query: { limit: 50, epoch: 1 } } },
			{
				params: {
					path: { service: 'wolfbet' },
					query: { metric: 'profit', from: 'a', to: 'b', epoch: 1 }
				}
			}
		]);
		expect(source.live).toBe(false);
	});

	it("reads one Telegram user's own history and never sends a lane", async () => {
		const source = tgSource(42);
		await source.series({ metric: 'profit', from: 'a', to: 'b', lane: 2 });
		await source.sessions({ limit: 100, since: 'a', lane: 2 });
		expect(calls).toEqual([
			{
				path: '/tg/{uid}/series',
				options: { params: { path: { uid: 42 }, query: { metric: 'profit', from: 'a', to: 'b' } } }
			},
			{
				path: '/tg/{uid}/sessions',
				options: {
					params: { path: { uid: 42 }, query: { limit: 100, since: 'a', cursor: undefined } }
				}
			}
		]);
		expect(source.sessionHref(7, 3)).toBe('/tg/42/sessions/7/3');
	});
});
