import { describe, expect, it } from 'vitest';
import { parseMessage } from './protocol';

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
