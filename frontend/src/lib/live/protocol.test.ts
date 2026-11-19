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
