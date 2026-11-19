import { addDropSamples, type DropSamples } from '#lib/system';
import { prependRows, TAPE_MAX } from '#lib/tape';
import {
	CLOSE_FORBIDDEN,
	CLOSE_SIGNED_OUT,
	parseMessage,
	type Candle,
	type CommentEvent,
	type Lanes,
	type SystemMessage,
	type TapeRow
} from './protocol';

export const STALE_MS = 6000;
const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 30_000;

export type Connection = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'signed_out';

export interface SocketLike {
	onopen: ((event: Event) => void) | null;
	onmessage: ((event: MessageEvent) => void) | null;
	onclose: ((event: CloseEvent) => void) | null;
	send(data: string): void;
	close(code?: number): void;
}

export class SignedOut extends Error {}

export interface LiveDeps {
	getTicket: () => Promise<string>;
	openSocket: (ticket: string) => SocketLike;
	onSignedOut: () => void;
	now?: () => number;
	frame?: (run: () => void) => void;
	after?: (run: () => void, ms: number) => () => void;
	random?: () => number;
}

/**
 * One live connection: fetch a one-time ticket, open the socket, subscribe, and fold the
 * server's snapshots and per-tick diffs into `lanes`. Frames are queued and applied once per
 * animation frame, and every changed lane becomes a new object while untouched lanes keep
 * theirs, so a 10 Hz feed redraws only what moved. A dropped socket reconnects with a fresh
 * ticket after a jittered, growing delay and resubscribes; being signed out stops it.
 */
export class LiveStore {
	lanes: Lanes = $state({});
	tape: TapeRow[] = $state.raw([]);
	system: SystemMessage | null = $state.raw(null);
	dropSamples: DropSamples = $state.raw({});
	connection: Connection = $state('idle');
	lastTickAt: number | null = $state(null);
	now: number = $state(0);
	stale: boolean = $derived(this.lastTickAt === null || this.now - this.lastTickAt > STALE_MS);

	#deps: Required<LiveDeps>;
	#topics: string[] = [];
	#socket: SocketLike | null = null;
	#queue: string[] = [];
	#flushPending = false;
	#attempt = 0;
	#running = false;
	#cancelRetry: (() => void) | null = null;
	#cancelClock: (() => void) | null = null;
	#candleListeners: Array<(candles: Candle[]) => void> = [];
	#commentListeners: Array<(event: CommentEvent) => void> = [];

	constructor(deps: LiveDeps) {
		this.#deps = {
			now: () => Date.now(),
			frame: (run) => requestAnimationFrame(() => run()),
			after: (run, ms) => {
				const id = setTimeout(run, ms);
				return () => clearTimeout(id);
			},
			random: Math.random,
			...deps
		};
		this.now = this.#deps.now();
	}

	start(topic: string): void {
		if (!this.#topics.includes(topic)) this.#topics.push(topic);
		if (this.#running) {
			this.#send({ op: 'subscribe', topic });
			return;
		}
		this.#running = true;
		this.#tickClock();
		void this.#connect();
	}

	stop(): void {
		this.#running = false;
		this.#topics = [];
		this.#cancelRetry?.();
		this.#cancelClock?.();
		const socket = this.#socket;
		this.#socket = null;
		if (socket) {
			socket.onclose = null;
			socket.close(1000);
		}
		this.connection = 'idle';
	}

	/** Call `listener` with each frame's new 1 s candles; returns the unsubscribe function. */
	onCandles(listener: (candles: Candle[]) => void): () => void {
		this.#candleListeners.push(listener);
		return () => {
			this.#candleListeners = this.#candleListeners.filter((l) => l !== listener);
		};
	}

	/** Call `listener` for each comment change; returns the unsubscribe function. */
	onComment(listener: (event: CommentEvent) => void): () => void {
		this.#commentListeners.push(listener);
		return () => {
			this.#commentListeners = this.#commentListeners.filter((l) => l !== listener);
		};
	}

	/** The delay before reconnect attempt `attempt` (0-based): doubling, capped, jittered. */
	backoff(attempt: number): number {
		const base = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt);
		return base * (0.5 + this.#deps.random());
	}

	#tickClock(): void {
		this.now = this.#deps.now();
		if (this.#running) this.#cancelClock = this.#deps.after(() => this.#tickClock(), 1000);
	}

	async #connect(): Promise<void> {
		if (!this.#running) return;
		this.connection = this.#attempt === 0 ? 'connecting' : 'reconnecting';
		let ticket: string;
		try {
			ticket = await this.#deps.getTicket();
		} catch (error) {
			if (error instanceof SignedOut) return this.#signedOut();
			return this.#retry();
		}
		if (!this.#running) return;
		const socket = this.#deps.openSocket(ticket);
		this.#socket = socket;
		socket.onopen = () => {
			for (const topic of this.#topics) this.#send({ op: 'subscribe', topic });
		};
		socket.onmessage = (event) => {
			if (typeof event.data === 'string') this.#enqueue(event.data);
		};
		socket.onclose = (event) => {
			this.#socket = null;
			if (event.code === CLOSE_SIGNED_OUT || event.code === CLOSE_FORBIDDEN) {
				return this.#signedOut();
			}
			this.#retry();
		};
	}

	#retry(): void {
		if (!this.#running) return;
		this.connection = 'reconnecting';
		const delay = this.backoff(this.#attempt);
		this.#attempt += 1;
		this.#cancelRetry = this.#deps.after(() => void this.#connect(), delay);
	}

	#signedOut(): void {
		this.#running = false;
		this.#cancelClock?.();
		this.connection = 'signed_out';
		this.#deps.onSignedOut();
	}

	#send(message: object): void {
		this.#socket?.send(JSON.stringify(message));
	}

	#enqueue(raw: string): void {
		this.#queue.push(raw);
		if (this.#flushPending) return;
		this.#flushPending = true;
		this.#deps.frame(() => this.#flush());
	}

	#flush(): void {
		this.#flushPending = false;
		const frames = this.#queue;
		this.#queue = [];
		let lanes = this.lanes;
		let tape = this.tape;
		let fresh = false;
		const candles: Candle[] = [];
		const comments: CommentEvent[] = [];
		for (const raw of frames) {
			const message = parseMessage(raw);
			if (message === null) continue;
			this.#attempt = 0;
			this.connection = 'live';
			if (message.op === 'snapshot') {
				lanes = { ...message.lanes };
				tape = prependRows([], message.tape, TAPE_MAX);
				fresh = true;
			} else if (message.op === 'comment') {
				comments.push(message);
			} else if (message.op === 'system') {
				this.system = message;
				this.dropSamples = addDropSamples(this.dropSamples, message.publisher, this.#deps.now());
			} else if (message.op === 'tick') {
				const next = { ...lanes };
				for (const [key, diff] of Object.entries(message.lanes)) {
					const lane = Number(key);
					next[lane] = { ...lanes[lane], ...diff };
				}
				lanes = next;
				candles.push(...message.candles);
				if (message.tape.length) tape = prependRows(tape, message.tape, TAPE_MAX);
				fresh = true;
			}
		}
		if (lanes !== this.lanes) this.lanes = lanes;
		if (tape !== this.tape) this.tape = tape;
		if (candles.length) for (const listener of this.#candleListeners) listener(candles);
		for (const event of comments) for (const listener of this.#commentListeners) listener(event);
		if (fresh) {
			this.now = this.#deps.now();
			this.lastTickAt = this.now;
		}
	}
}
