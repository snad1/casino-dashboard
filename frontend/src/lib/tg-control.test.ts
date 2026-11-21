import { beforeEach, describe, expect, it, vi } from 'vitest';

const posts: Array<{ path: string; options: Record<string, unknown> }> = [];
let reply: { ok: boolean; status: number; error?: unknown } = { ok: true, status: 200 };
const toasts: Array<[string, string]> = [];

vi.mock('./session.svelte', () => ({
	api: {
		POST: (path: string, options: Record<string, unknown>) => {
			posts.push({ path, options });
			return Promise.resolve(
				reply.ok
					? { data: { slots: [1] }, response: { ok: true, status: 200 } }
					: { error: reply.error, response: { ok: false, status: reply.status } }
			);
		}
	}
}));
vi.mock('./toast.svelte', () => ({
	toast: (tone: string, text: string) => toasts.push([tone, text])
}));

const { tgControl } = await import('./tg-control.svelte');

beforeEach(() => {
	posts.length = 0;
	toasts.length = 0;
	reply = { ok: true, status: 200 };
});

describe('tgControl', () => {
	it('sends a confirmed press once, for the path uid, with its own key', async () => {
		tgControl.ask(42, 'pause', 1);
		expect(tgControl.open).toBe(true);
		expect(tgControl.copy?.title).toBe('Pause this session?');
		const before = tgControl.done;
		await tgControl.confirm();
		await tgControl.confirm();
		expect(posts).toHaveLength(1);
		const [sent] = posts;
		if (!sent) throw new Error('nothing was sent');
		const { path, options } = sent;
		expect(path).toBe('/tg/{uid}/control/{action}');
		const params = options.params as { path: unknown; header: Record<string, string> };
		expect(params.path).toEqual({ uid: 42, action: 'pause' });
		expect(params.header['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/);
		expect(options.body).toEqual({ slot: 1 });
		expect(tgControl.done).toBe(before + 1);
		expect(tgControl.open).toBe(false);
		expect(toasts).toEqual([['success', 'Pause: done.']]);
	});

	it('starts without a slot and gives every press a new key', async () => {
		tgControl.ask(42, 'start');
		const first = tgControl.pending?.key;
		tgControl.cancel();
		tgControl.ask(42, 'start');
		expect(tgControl.pending?.key).not.toBe(first);
		await tgControl.confirm();
		expect(posts[0]?.options.body).toEqual({});
	});

	it("shows the api's own reason when the bot refuses", async () => {
		reply = { ok: false, status: 404, error: { detail: 'No session is running there.' } };
		tgControl.ask(42, 'stop', 2);
		await tgControl.confirm();
		expect(toasts).toEqual([['error', 'No session is running there.']]);
		expect(tgControl.pending).toBeNull();
	});
});
