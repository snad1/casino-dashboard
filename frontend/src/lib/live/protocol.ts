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
	balance?: number | null;
	currency?: string | null;
	bps_1s?: number;
	api_ms_p50?: number | null;
	api_ms_p99?: number | null;
	[key: string]: unknown;
}

export type LaneStatus = 'running' | 'paused' | 'stopped' | 'stale' | 'down' | 'events_off';
export type Lanes = Record<number, LaneFields>;

export type ServerMessage =
	| { op: 'snapshot'; topic: string; lanes: Lanes }
	| { op: 'tick'; topic: string; lanes: Lanes }
	| { op: 'hb' }
	| { op: 'error'; topic?: string; detail: string }
	| { op: 'system'; topic: string };

export const CLOSE_SIGNED_OUT = 4401;
export const CLOSE_FORBIDDEN = 4403;

export function topicFor(service: string): string {
	return `svc:${service}`;
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
		case 'tick':
			if (typeof m.topic !== 'string' || !isLanes(m.lanes)) return null;
			return { op: m.op, topic: m.topic, lanes: m.lanes };
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
