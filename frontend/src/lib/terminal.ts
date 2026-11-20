import { resolve } from '$app/paths';

/**
 * The terminal socket's wire format: every frame is binary with a one-byte op. `0x00` carries
 * terminal bytes both ways; from the page `0x01` is a resize (cols, rows as big-endian u16),
 * from the api `0x01` is a JSON notice: the session when it opens, the reason before it closes.
 */
export const OP_DATA = 0;
export const OP_CONTROL = 1;
/** The api closes the socket on a bigger frame; input is split below it. */
export const MAX_INPUT_BYTES = 16 * 1024 - 1;

export type ServerMessage =
	| { kind: 'output'; data: Uint8Array }
	| { kind: 'hello'; sessionId: string; idleS: number; maxS: number }
	| { kind: 'end'; reason: string };

const encoder = new TextEncoder();

export function decodeFrame(buffer: ArrayBuffer): ServerMessage | null {
	const bytes = new Uint8Array(buffer);
	if (bytes.length === 0) return null;
	if (bytes[0] === OP_DATA) return { kind: 'output', data: bytes.subarray(1) };
	if (bytes[0] !== OP_CONTROL) return null;
	let body: unknown;
	try {
		body = JSON.parse(new TextDecoder().decode(bytes.subarray(1)));
	} catch {
		return null;
	}
	if (typeof body !== 'object' || body === null) return null;
	const message = body as Record<string, unknown>;
	if (typeof message.reason === 'string') return { kind: 'end', reason: message.reason };
	if (
		typeof message.session_id === 'string' &&
		typeof message.idle_s === 'number' &&
		typeof message.max_s === 'number'
	) {
		return {
			kind: 'hello',
			sessionId: message.session_id,
			idleS: message.idle_s,
			maxS: message.max_s
		};
	}
	return null;
}

/** Keys or a paste as one or more input frames, each under the api's frame cap. */
export function inputFrames(data: string | Uint8Array): Uint8Array<ArrayBuffer>[] {
	const bytes = typeof data === 'string' ? encoder.encode(data) : data;
	const frames: Uint8Array<ArrayBuffer>[] = [];
	for (let start = 0; start < bytes.length; start += MAX_INPUT_BYTES) {
		const piece = bytes.subarray(start, start + MAX_INPUT_BYTES);
		const frame = new Uint8Array(piece.length + 1);
		frame[0] = OP_DATA;
		frame.set(piece, 1);
		frames.push(frame);
	}
	return frames;
}

export function resizeFrame(cols: number, rows: number): Uint8Array<ArrayBuffer> {
	const frame = new Uint8Array(5);
	const view = new DataView(frame.buffer);
	frame[0] = OP_CONTROL;
	view.setUint16(1, cols);
	view.setUint16(3, rows);
	return frame;
}

/** What the phone key bar sends. */
export const KEYS = {
	Esc: '\x1b',
	Tab: '\t',
	Up: '\x1b[A',
	Down: '\x1b[B',
	Right: '\x1b[C',
	Left: '\x1b[D'
} as const;
export type KeyName = keyof typeof KEYS;

/** Ctrl plus a key, as a terminal sends it; null for keys that have no control code. */
export function ctrl(key: string): string | null {
	if (key.length !== 1) return null;
	const code = key.toUpperCase().charCodeAt(0);
	if (code === 0x3f) return '\x7f';
	if (code < 0x40 || code > 0x5f) return null;
	return String.fromCharCode(code & 0x1f);
}

/** Only web links open from terminal output, always in a new tab without an opener. */
export function safeLink(uri: string): string | null {
	try {
		const url = new URL(uri);
		return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
	} catch {
		return null;
	}
}

const CLOSED_TEXT = 'The terminal is closed.';
const CLOSED: Record<string, string> = {
	closed: CLOSED_TEXT,
	idle: 'The terminal closed after 10 minutes without input.',
	max: 'The terminal closed after 60 minutes, the longest a session may run.',
	exited: 'The shell exited.',
	recording_full: 'The terminal closed because its recording is full.',
	shutdown: 'The terminal closed because the host agent restarted.',
	signed_out: 'You were signed out, so the terminal closed.',
	forbidden: 'The terminal closed because you no longer have access to it.',
	busy: 'A terminal is already open. Close it first.',
	unavailable: 'The terminal is unavailable right now. Try again in a moment.',
	unsupported: 'The terminal closed after an unexpected message from this page. Reload it.',
	too_big: 'The terminal closed because a message was too large. Reload the page.'
};
export const NOT_OPENED =
	'The terminal could not open. Check your connection and try again, or reload the page.';

export function closeText(reason: string | null): string {
	return reason === null ? NOT_OPENED : (CLOSED[reason] ?? CLOSED_TEXT);
}

export function terminalSocketUrl(ticket: string, cols: number, rows: number): string {
	const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
	const query = `ticket=${encodeURIComponent(ticket)}&cols=${cols}&rows=${rows}`;
	return `${scheme}://${location.host}${resolve('/(app)')}ws/terminal?${query}`;
}

/** `m:ss` for a countdown; never below zero. */
export function clock(seconds: number): string {
	const s = Math.max(0, Math.ceil(seconds));
	return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export interface CastEvent {
	at: number;
	kind: 'o' | 'r';
	data: string;
}
export interface Cast {
	cols: number;
	rows: number;
	events: CastEvent[];
	duration: number;
}

/** An asciicast v2 recording: output and resize events in time order; input is not replayed. */
export function parseCast(text: string): Cast | null {
	const lines = text.split('\n').filter((line) => line.trim() !== '');
	let header: unknown;
	try {
		header = JSON.parse(lines[0] ?? '');
	} catch {
		return null;
	}
	const h = header as Record<string, unknown> | null;
	if (h === null || typeof h !== 'object' || h.version !== 2) return null;
	const events: CastEvent[] = [];
	for (const line of lines.slice(1)) {
		let event: unknown;
		try {
			event = JSON.parse(line);
		} catch {
			continue;
		}
		if (!Array.isArray(event) || event.length !== 3) continue;
		const [at, kind, data] = event as unknown[];
		if (typeof at !== 'number' || typeof data !== 'string') continue;
		if (kind === 'o' || kind === 'r') events.push({ at, kind, data });
	}
	events.sort((a, b) => a.at - b.at);
	return {
		cols: typeof h.width === 'number' ? h.width : 80,
		rows: typeof h.height === 'number' ? h.height : 24,
		events,
		duration: events.at(-1)?.at ?? 0
	};
}

/** The index after the last event due at `seconds` into the recording, from `from` on. */
export function dueUntil(events: CastEvent[], from: number, seconds: number): number {
	let index = from;
	while ((events[index]?.at ?? Infinity) <= seconds) index += 1;
	return index;
}

/** `100x40` from a resize event, or null. */
export function castSize(data: string): { cols: number; rows: number } | null {
	const match = /^(\d{1,3})x(\d{1,3})$/.exec(data);
	return match ? { cols: Number(match[1]), rows: Number(match[2]) } : null;
}

/** The same events with every pause longer than `maxGap` seconds shortened to it. */
export function compressGaps(events: CastEvent[], maxGap: number): CastEvent[] {
	let shift = 0;
	let previous = 0;
	return events.map((event) => {
		const gap = event.at - previous;
		if (gap > maxGap) shift += gap - maxGap;
		previous = event.at;
		return { ...event, at: event.at - shift };
	});
}
