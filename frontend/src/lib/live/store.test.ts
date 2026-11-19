import { describe, expect, it, vi } from 'vitest';
import { LiveStore, SignedOut, STALE_MS, type SocketLike } from './store.svelte';

class FakeSocket implements SocketLike {
	onopen: SocketLike['onopen'] = null;
	onmessage: SocketLike['onmessage'] = null;
	onclose: SocketLike['onclose'] = null;
	sent: unknown[] = [];
	closed = false;

	constructor(readonly ticket: string) {}

	send(data: string) {
		this.sent.push(JSON.parse(data));
	}

	close() {
		this.closed = true;
	}

	open() {
		this.onopen?.(new Event('open'));
	}

	receive(message: object) {
		this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
	}

	drop(code = 1006) {
		this.onclose?.({ code } as CloseEvent);
	}
}

function rig(ticket: () => Promise<string> = async () => 't') {
	let clock = 1_000;
	const frames: Array<() => void> = [];
	const timers: Array<{ at: number; run: () => void; cancelled: boolean }> = [];
	const sockets: FakeSocket[] = [];
	const onSignedOut = vi.fn();
	const getTicket = vi.fn(ticket);
	const store = new LiveStore({
		getTicket,
		openSocket: (t) => {
			const socket = new FakeSocket(t);
			sockets.push(socket);
			return socket;
		},
		onSignedOut,
		now: () => clock,
		frame: (run) => frames.push(run),
		after: (run, ms) => {
			const timer = { at: clock + ms, run, cancelled: false };
			timers.push(timer);
			return () => (timer.cancelled = true);
		},
		random: () => 0.5
	});
	const flush = () => frames.splice(0).forEach((run) => run());
	const advance = async (ms: number) => {
		clock += ms;
		for (const timer of timers.filter((t) => !t.cancelled && t.at <= clock)) {
			timer.cancelled = true;
			timer.run();
		}
		await Promise.resolve();
		await Promise.resolve();
	};
	const settle = async () => {
		await Promise.resolve();
		await Promise.resolve();
	};
	const socket = (i: number): FakeSocket => {
		const found = sockets[i];
		if (!found) throw new Error(`socket ${i} was never opened`);
		return found;
	};
	return { store, sockets, socket, flush, advance, settle, onSignedOut, getTicket };
}

const snapshot = {
	op: 'snapshot',
	topic: 'svc:wolfbet',
	lanes: { 1: { profit: 1, status: 'running' }, 2: { profit: 2, status: 'running' } },
	tape: []
};

describe('live store', () => {
	it('subscribes on open and applies diffs once per frame, immutably', async () => {
		const { store, socket, flush, settle } = rig();
		store.start('svc:wolfbet');
		await settle();
		socket(0).open();
		expect(socket(0).sent).toEqual([{ op: 'subscribe', topic: 'svc:wolfbet' }]);

		socket(0).receive(snapshot);
		flush();
		const before = store.lanes;
		socket(0).receive({ op: 'tick', topic: 'svc:wolfbet', lanes: { 1: { profit: 1.5 } } });
		socket(0).receive({ op: 'tick', topic: 'svc:wolfbet', lanes: { 1: { total_bets: 9 } } });
		expect(store.lanes).toBe(before);
		flush();
		expect(store.lanes).not.toBe(before);
		expect(store.lanes[1]).toEqual({ profit: 1.5, status: 'running', total_bets: 9 });
		expect(store.lanes[2]).toBe(before[2]);
		expect(store.connection).toBe('live');
	});

	it('reconnects with a fresh ticket and resubscribes after a growing delay', async () => {
		let n = 0;
		const { store, sockets, socket, flush, advance, settle, getTicket } = rig(
			async () => `t${++n}`
		);
		store.start('svc:wolfbet');
		await settle();
		socket(0).open();
		socket(0).drop();
		expect(store.connection).toBe('reconnecting');
		expect(store.backoff(0)).toBe(500);
		expect(store.backoff(10)).toBe(30_000);
		await advance(499);
		expect(sockets).toHaveLength(1);
		await advance(1);
		expect(getTicket).toHaveBeenCalledTimes(2);
		expect(socket(1).ticket).toBe('t2');
		socket(1).open();
		expect(socket(1).sent).toEqual([{ op: 'subscribe', topic: 'svc:wolfbet' }]);
		socket(1).receive(snapshot);
		flush();
		expect(store.connection).toBe('live');
		socket(1).drop();
		await advance(500);
		expect(sockets).toHaveLength(3);
	});

	it('stops and asks for sign-in when the session is gone', async () => {
		const { store, sockets, socket, advance, settle, onSignedOut } = rig();
		store.start('svc:wolfbet');
		await settle();
		socket(0).drop(4401);
		expect(store.connection).toBe('signed_out');
		expect(onSignedOut).toHaveBeenCalledOnce();
		await advance(60_000);
		expect(sockets).toHaveLength(1);

		const expired = rig(async () => {
			throw new SignedOut();
		});
		expired.store.start('svc:wolfbet');
		await expired.settle();
		expect(expired.store.connection).toBe('signed_out');
		expect(expired.sockets).toHaveLength(0);
	});

	it('goes stale after 6 s without a tick and recovers on the next one', async () => {
		const { store, socket, flush, advance, settle } = rig();
		store.start('svc:wolfbet');
		await settle();
		socket(0).receive(snapshot);
		flush();
		expect(store.stale).toBe(false);
		await advance(STALE_MS + 1000);
		expect(store.stale).toBe(true);
		socket(0).receive({ op: 'hb' });
		flush();
		expect(store.stale).toBe(true);
		socket(0).receive({ op: 'tick', topic: 'svc:wolfbet', lanes: { 1: { profit: 3 } } });
		flush();
		expect(store.stale).toBe(false);
	});

	it('hands the candles of each frame to listeners once', async () => {
		const { store, socket, flush, settle } = rig();
		const seen: unknown[] = [];
		const off = store.onCandles((candles) => seen.push(candles));
		store.start('svc:wolfbet');
		await settle();
		const candle = { lane: 1, t: 5, profit: [0, 1, 0, 1], balance: [9, 9, 9, 9] };
		socket(0).receive({ op: 'tick', topic: 'svc:wolfbet', lanes: {}, candles: [candle] });
		socket(0).receive({ op: 'tick', topic: 'svc:wolfbet', lanes: {}, candles: [candle] });
		flush();
		expect(seen).toEqual([[candle, candle]]);
		off();
		socket(0).receive({ op: 'tick', topic: 'svc:wolfbet', lanes: {}, candles: [candle] });
		flush();
		expect(seen).toHaveLength(1);
	});

	it('stop closes the socket and cancels any retry', async () => {
		const { store, sockets, socket, advance, settle } = rig();
		store.start('svc:wolfbet');
		await settle();
		store.stop();
		expect(socket(0).closed).toBe(true);
		expect(store.connection).toBe('idle');
		await advance(60_000);
		expect(sockets).toHaveLength(1);
	});
});
