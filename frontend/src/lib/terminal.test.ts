import { describe, expect, it } from 'vitest';
import {
	castSize,
	clock,
	closeText,
	compressGaps,
	ctrl,
	decodeFrame,
	dueUntil,
	inputFrames,
	KEYS,
	MAX_INPUT_BYTES,
	NOT_OPENED,
	parseCast,
	resizeFrame,
	safeLink
} from './terminal';

function control(body: unknown): ArrayBuffer {
	const json = new TextEncoder().encode(JSON.stringify(body));
	const frame = new Uint8Array(json.length + 1);
	frame[0] = 1;
	frame.set(json, 1);
	return frame.buffer;
}

describe('frames', () => {
	it('reads output, the opening notice and the closing reason', () => {
		const out = decodeFrame(new Uint8Array([0, 104, 105]).buffer);
		expect(out).toEqual({ kind: 'output', data: new Uint8Array([104, 105]) });
		expect(decodeFrame(control({ session_id: 'ab', idle_s: 600, max_s: 3600 }))).toEqual({
			kind: 'hello',
			sessionId: 'ab',
			idleS: 600,
			maxS: 3600
		});
		expect(decodeFrame(control({ reason: 'idle' }))).toEqual({ kind: 'end', reason: 'idle' });
	});

	it('ignores empty, unknown and malformed frames', () => {
		expect(decodeFrame(new ArrayBuffer(0))).toBeNull();
		expect(decodeFrame(new Uint8Array([7, 1]).buffer)).toBeNull();
		expect(decodeFrame(new Uint8Array([1, 123]).buffer)).toBeNull();
		expect(decodeFrame(control({ other: 1 }))).toBeNull();
	});

	it('splits a big paste under the frame cap and keeps every byte', () => {
		const paste = 'x'.repeat(MAX_INPUT_BYTES * 2 + 5);
		const frames = inputFrames(paste);
		expect(frames).toHaveLength(3);
		expect(frames.every((f) => f[0] === 0 && f.length <= MAX_INPUT_BYTES + 1)).toBe(true);
		expect(frames.reduce((n, f) => n + f.length - 1, 0)).toBe(paste.length);
		expect(inputFrames('é')[0]).toEqual(new Uint8Array([0, 0xc3, 0xa9]));
	});

	it('encodes a resize as big-endian cols and rows', () => {
		expect(resizeFrame(300, 40)).toEqual(new Uint8Array([1, 1, 44, 0, 40]));
	});
});

describe('keys', () => {
	it('maps the key bar and Ctrl the way a terminal does', () => {
		expect(KEYS.Esc).toBe('\x1b');
		expect(KEYS.Up).toBe('\x1b[A');
		expect(ctrl('c')).toBe('\x03');
		expect(ctrl('C')).toBe('\x03');
		expect(ctrl('[')).toBe('\x1b');
		expect(ctrl('?')).toBe('\x7f');
		expect(ctrl('1')).toBeNull();
		expect(ctrl('ab')).toBeNull();
	});
});

describe('links and text', () => {
	it('opens only http and https links', () => {
		expect(safeLink('https://example.com/a')).toBe('https://example.com/a');
		expect(safeLink('javascript:alert(1)')).toBeNull();
		expect(safeLink('data:text/html,x')).toBeNull();
		expect(safeLink('not a url')).toBeNull();
	});

	it('explains each way the terminal closes', () => {
		expect(closeText('idle')).toContain('10 minutes');
		expect(closeText('busy')).toBe('A terminal is already open. Close it first.');
		expect(closeText('something new')).toBe('The terminal is closed.');
		expect(closeText(null)).toBe(NOT_OPENED);
	});

	it('formats a countdown', () => {
		expect(clock(600)).toBe('10:00');
		expect(clock(61.2)).toBe('1:02');
		expect(clock(-5)).toBe('0:00');
	});
});

describe('recordings', () => {
	const cast = [
		'{"version": 2, "width": 100, "height": 30, "timestamp": 1}',
		'[0.5, "o", "b"]',
		'[0.1, "o", "a"]',
		'[0.2, "i", "secret"]',
		'[0.3, "r", "120x40"]',
		'not json',
		''
	].join('\n');

	it('keeps output and resizes in time order and never replays input', () => {
		const parsed = parseCast(cast);
		expect(parsed).not.toBeNull();
		expect(parsed?.cols).toBe(100);
		expect(parsed?.events.map((e) => e.data)).toEqual(['a', '120x40', 'b']);
		expect(parsed?.duration).toBe(0.5);
	});

	it('refuses anything but asciicast v2', () => {
		expect(parseCast('{"version": 1}')).toBeNull();
		expect(parseCast('')).toBeNull();
	});

	it('finds the events due by a time and reads resize sizes', () => {
		const events = parseCast(cast)?.events ?? [];
		expect(dueUntil(events, 0, 0.3)).toBe(2);
		expect(dueUntil(events, 2, 1)).toBe(3);
		expect(castSize('120x40')).toEqual({ cols: 120, rows: 40 });
		expect(castSize('big')).toBeNull();
	});

	it('shortens long pauses and keeps short ones', () => {
		const events = [
			{ at: 1, kind: 'o' as const, data: 'a' },
			{ at: 31, kind: 'o' as const, data: 'b' },
			{ at: 31.5, kind: 'o' as const, data: 'c' }
		];
		expect(compressGaps(events, 2).map((e) => e.at)).toEqual([1, 3, 3.5]);
	});
});
