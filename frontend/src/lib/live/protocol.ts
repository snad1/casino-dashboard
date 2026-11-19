/** One lane as the api's live model reports it; a tick carries only the changed keys. */
export interface LaneFields {
	session_id?: number | null;
	status?: LaneStatus;
	total_bets?: number;
	wins?: number;
	losses?: number;
	profit?: number;
	wagered?: number;
	roi?: number;
	current_streak?: number;
	max_win_streak?: number;
	max_loss_streak?: number;
	balance?: number | null;
	currency?: string | null;
	bps_1s?: number;
	api_ms_p50?: number | null;
	api_ms_p99?: number | null;
	[key: string]: unknown;
}

export type LaneStatus = 'running' | 'paused' | 'stopped' | 'stale' | 'down' | 'events_off';
export type Lanes = Record<number, LaneFields>;

/** One lane's second: open, high, low and close of its session profit and its balance. */
/** One bet as the live tape carries it. `seq` counts per daemon process and lane. */
export interface TapeRow {
	lane: number;
	seq: number;
	ts_ms: number;
	amount: number;
	multiplier: number;
	result: number;
	state: string;
	profit: number;
	balance: number;
}

export interface Candle {
	lane: number;
	t: number;
	profit: number[];
	balance: number[];
}

export type ServerMessage =
	| { op: 'snapshot'; topic: string; lanes: Lanes; tape: TapeRow[] }
	| { op: 'tick'; topic: string; lanes: Lanes; candles: Candle[]; tape: TapeRow[] }
	| { op: 'hb' }
	| { op: 'error'; topic?: string; detail: string }
	| { op: 'system'; topic: string };

export const CLOSE_SIGNED_OUT = 4401;
export const CLOSE_FORBIDDEN = 4403;

export function topicFor(service: string): string {
	return `svc:${service}`;
}

function isCandle(value: unknown): value is Candle {
	if (typeof value !== 'object' || value === null) return false;
	const c = value as Record<string, unknown>;
	const ohlc = (v: unknown) =>
		Array.isArray(v) && v.length === 4 && v.every((n) => typeof n === 'number');
	return typeof c.lane === 'number' && typeof c.t === 'number' && ohlc(c.profit) && ohlc(c.balance);
}

const TAPE_NUMBERS = [
	'lane',
	'seq',
	'ts_ms',
	'amount',
	'multiplier',
	'result',
	'profit',
	'balance'
];

function isTapeRow(value: unknown): value is TapeRow {
	if (typeof value !== 'object' || value === null) return false;
	const r = value as Record<string, unknown>;
	return TAPE_NUMBERS.every((key) => typeof r[key] === 'number') && typeof r.state === 'string';
}

function tapeOf(value: unknown): TapeRow[] {
	return Array.isArray(value) ? value.filter(isTapeRow) : [];
}

function isLanes(value: unknown): value is Lanes {
	if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
	return Object.entries(value).every(
		([key, lane]) => /^\d+$/.test(key) && typeof lane === 'object' && lane !== null
	);
}

/** A server frame, or null when it is not one this client understands. */
export function parseMessage(raw: string): ServerMessage | null {
	let message: unknown;
	try {
		message = JSON.parse(raw);
	} catch {
		return null;
	}
	if (typeof message !== 'object' || message === null) return null;
	const m = message as Record<string, unknown>;
	switch (m.op) {
		case 'hb':
			return { op: 'hb' };
		case 'snapshot':
			if (typeof m.topic !== 'string' || !isLanes(m.lanes)) return null;
			return { op: 'snapshot', topic: m.topic, lanes: m.lanes, tape: tapeOf(m.tape) };
		case 'tick': {
			if (typeof m.topic !== 'string' || !isLanes(m.lanes)) return null;
			const candles = Array.isArray(m.candles) ? m.candles.filter(isCandle) : [];
			return { op: 'tick', topic: m.topic, lanes: m.lanes, candles, tape: tapeOf(m.tape) };
		}
		case 'error':
			return typeof m.detail === 'string'
				? {
						op: 'error',
						detail: m.detail,
						topic: typeof m.topic === 'string' ? m.topic : undefined
					}
				: null;
		case 'system':
			return typeof m.topic === 'string' ? { op: 'system', topic: m.topic } : null;
		default:
			return null;
	}
}
