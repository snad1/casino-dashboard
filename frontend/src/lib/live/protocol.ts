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
	base_bet?: number | null;
	current_bet?: number | null;
	lane_split?: string | null;
	run_profit?: number | null;
	stop_reason?: string | null;
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

/** A comment on this target changed; the page reloads it to get the text and its own rights. */
export interface CommentEvent {
	kind: 'session' | 'bet' | 'chart';
	epoch_id: number | null;
	session_id: number | null;
	bet_id: number | null;
	at: string | null;
}

/** One output line of a control job, numbered from 0; a resubscribe starts again at 0. */
export interface JobLine {
	topic: string;
	seq: number;
	stream: 'stdout' | 'stderr';
	line: string;
}

export interface JobEnd {
	topic: string;
	status: string;
	exit_code: number | null;
}

export type JobMessage = ({ op: 'job' } & JobLine) | ({ op: 'job_end' } & JobEnd);

export type ServerMessage =
	| JobMessage
	| ({ op: 'comment'; topic: string } & CommentEvent)
	| { op: 'snapshot'; topic: string; lanes: Lanes; tape: TapeRow[] }
	| { op: 'tick'; topic: string; lanes: Lanes; candles: Candle[]; tape: TapeRow[] }
	| { op: 'hb' }
	| { op: 'error'; topic?: string; detail: string }
	| ({ op: 'system'; topic: string } & SystemMessage);

export interface HostMetrics {
	cpu_pct: number | null;
	load: number[] | null;
	mem_total: number | null;
	mem_available: number | null;
	disk_total: number | null;
	disk_free: number | null;
	net_rx_bps: number | null;
	net_tx_bps: number | null;
}

export interface UnitMetrics {
	unit: string;
	cpu_pct: number | null;
	rss_bytes: number | null;
}

export interface ContainerMetrics {
	service: string;
	cpu_pct: number | null;
	memory_bytes: number | null;
}

export interface PublisherCounters {
	enabled: boolean;
	connected: boolean;
	sent: number;
	queued: number;
	dropped: number;
}

/** One system sample: host, units and containers from the agent, publisher counters per lane. */
export interface SystemMessage {
	ts_ms: number;
	agent: 'ok' | 'unavailable' | 'pending';
	host: HostMetrics | null;
	units: UnitMetrics[];
	containers: ContainerMetrics[];
	publisher: Record<number, PublisherCounters>;
}

export const CLOSE_SIGNED_OUT = 4401;
export const CLOSE_FORBIDDEN = 4403;

export function topicFor(service: string): string {
	return `svc:${service}`;
}

export function systemTopicFor(service: string): string {
	return `svc:${service}:system`;
}

export function commentsTopicFor(service: string): string {
	return `svc:${service}:comments`;
}

export function jobTopicFor(service: string, jobId: string): string {
	return `svc:${service}:job:${jobId}`;
}

const KINDS = new Set(['session', 'bet', 'chart']);
const idOrNull = (value: unknown): number | null => (typeof value === 'number' ? value : null);

const asObject = (value: unknown): Record<string, unknown> | null =>
	typeof value === 'object' && value !== null && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
const asList = <T>(value: unknown, keep: (item: Record<string, unknown>) => boolean): T[] =>
	Array.isArray(value) ? (value.map(asObject).filter((v) => v !== null && keep(v)) as T[]) : [];

function systemOf(m: Record<string, unknown>): SystemMessage {
	const publisher: Record<number, PublisherCounters> = {};
	for (const [lane, counters] of Object.entries(asObject(m.publisher) ?? {})) {
		const c = asObject(counters);
		if (/^\d+$/.test(lane) && c && typeof c.sent === 'number' && typeof c.dropped === 'number') {
			publisher[Number(lane)] = c as unknown as PublisherCounters;
		}
	}
	const agent = m.agent === 'ok' || m.agent === 'pending' ? m.agent : 'unavailable';
	return {
		ts_ms: typeof m.ts_ms === 'number' ? m.ts_ms : 0,
		agent,
		host: (asObject(m.host) as HostMetrics | null) ?? null,
		units: asList<UnitMetrics>(m.units, (u) => typeof u.unit === 'string'),
		containers: asList<ContainerMetrics>(m.containers, (c) => typeof c.service === 'string'),
		publisher
	};
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
			return typeof m.topic === 'string' ? { op: 'system', topic: m.topic, ...systemOf(m) } : null;
		case 'job':
			if (
				typeof m.topic !== 'string' ||
				typeof m.seq !== 'number' ||
				typeof m.line !== 'string' ||
				(m.stream !== 'stdout' && m.stream !== 'stderr')
			) {
				return null;
			}
			return { op: 'job', topic: m.topic, seq: m.seq, stream: m.stream, line: m.line };
		case 'job_end':
			if (typeof m.topic !== 'string' || typeof m.status !== 'string') return null;
			return {
				op: 'job_end',
				topic: m.topic,
				status: m.status,
				exit_code: typeof m.exit_code === 'number' ? m.exit_code : null
			};
		case 'comment':
			if (typeof m.topic !== 'string' || typeof m.kind !== 'string' || !KINDS.has(m.kind)) {
				return null;
			}
			return {
				op: 'comment',
				topic: m.topic,
				kind: m.kind as CommentEvent['kind'],
				epoch_id: idOrNull(m.epoch_id),
				session_id: idOrNull(m.session_id),
				bet_id: idOrNull(m.bet_id),
				at: typeof m.at === 'string' ? m.at : null
			};
		default:
			return null;
	}
}
