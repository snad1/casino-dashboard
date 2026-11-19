import { describe, expect, it } from 'vitest';
import { parseMessage } from './protocol';

describe('parseMessage', () => {
	it('reads the frames the server sends', () => {
		expect(parseMessage('{"op":"hb"}')).toEqual({ op: 'hb' });
		expect(
			parseMessage('{"op":"tick","topic":"svc:wolfbet","lanes":{"1":{"profit":1}},"tape":[]}')
		).toEqual({ op: 'tick', topic: 'svc:wolfbet', lanes: { 1: { profit: 1 } }, candles: [] });
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
