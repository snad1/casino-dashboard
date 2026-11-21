import { describe, expect, it } from 'vitest';
import { jobTopicFor, parseMessage, userTopicFor } from './protocol';

describe('parseMessage', () => {
	it('reads the frames the server sends', () => {
		expect(parseMessage('{"op":"hb"}')).toEqual({ op: 'hb' });
		expect(
			parseMessage('{"op":"tick","topic":"svc:wolfbet","lanes":{"1":{"profit":1}},"tape":[]}')
		).toEqual({
			op: 'tick',
			topic: 'svc:wolfbet',
			lanes: { 1: { profit: 1 } },
			candles: [],
			tape: []
		});
	});

	it('keeps only well-formed tape rows', () => {
		const good = {
			lane: 1,
			seq: 4,
			ts_ms: 9,
			amount: 1,
			multiplier: 2,
			result: 60,
			state: 'win',
			profit: 1,
			balance: 10
		};
		const message = parseMessage(
			JSON.stringify({ op: 'snapshot', topic: 'svc:wolfbet', lanes: {}, tape: [good, { lane: 1 }] })
		);
		expect(message).toEqual({ op: 'snapshot', topic: 'svc:wolfbet', lanes: {}, tape: [good] });
	});

	it('reads system samples and drops malformed parts', () => {
		const raw = {
			op: 'system',
			topic: 'svc:wolfbet:system',
			ts_ms: 5,
			agent: 'ok',
			host: { cpu_pct: 12 },
			units: [{ unit: 'wolfbet', cpu_pct: 1, rss_bytes: 2 }, { cpu_pct: 3 }],
			containers: 'nope',
			publisher: {
				1: { enabled: true, connected: true, sent: 4, queued: 0, dropped: 1 },
				x: { sent: 1, dropped: 1 },
				2: { sent: 'many' }
			}
		};
		expect(parseMessage(JSON.stringify(raw))).toEqual({
			op: 'system',
			topic: 'svc:wolfbet:system',
			ts_ms: 5,
			agent: 'ok',
			host: { cpu_pct: 12 },
			units: [{ unit: 'wolfbet', cpu_pct: 1, rss_bytes: 2 }],
			containers: [],
			publisher: { 1: { enabled: true, connected: true, sent: 4, queued: 0, dropped: 1 } }
		});
		const odd = parseMessage(JSON.stringify({ op: 'system', topic: 't', agent: 'weird' }));
		expect(odd).toMatchObject({ agent: 'unavailable', host: null, units: [], publisher: {} });
	});

	it('reads comment change signals', () => {
		const raw = {
			op: 'comment',
			topic: 'svc:wolfbet:comments',
			kind: 'bet',
			epoch_id: 1,
			session_id: null,
			bet_id: 4,
			at: null
		};
		expect(parseMessage(JSON.stringify(raw))).toEqual(raw);
		expect(parseMessage(JSON.stringify({ ...raw, kind: 'other' }))).toBeNull();
	});

	it('reads job output lines and the job end', () => {
		const topic = jobTopicFor('wolfbet', 'ab'.repeat(16));
		expect(topic).toBe('svc:wolfbet:job:' + 'ab'.repeat(16));
		const line = { op: 'job', topic, seq: 3, stream: 'stderr', line: '<b>x</b>' };
		expect(parseMessage(JSON.stringify(line))).toEqual(line);
		const end = { op: 'job_end', topic, status: 'failed', exit_code: 2 };
		expect(parseMessage(JSON.stringify(end))).toEqual(end);
		expect(parseMessage(JSON.stringify({ ...end, exit_code: 'x' }))).toEqual({
			...end,
			exit_code: null
		});
		expect(parseMessage(JSON.stringify({ ...line, stream: 'tty' }))).toBeNull();
		expect(parseMessage(JSON.stringify({ ...line, seq: '3' }))).toBeNull();
	});

	it('ignores anything else', () => {
		for (const raw of [
			'not json',
			'null',
			'{"op":"tick","topic":"svc:wolfbet","lanes":{"x":{}}}',
			'{"op":"snapshot","lanes":{}}',
			'{"op":"surprise"}'
		]) {
			expect(parseMessage(raw)).toBeNull();
		}
	});
});

describe('userTopicFor', () => {
	it("names one Telegram user's live topic", () => {
		expect(userTopicFor('wolfbet-tg', 42)).toBe('svc:wolfbet-tg:user:42');
	});
});
