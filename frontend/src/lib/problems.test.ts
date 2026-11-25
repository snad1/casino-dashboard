import { describe, expect, it } from 'vitest';
import { AGENT_SILENT, problems } from './problems';

describe('problems', () => {
	it('names a lane that is down while another lane bets, with the lane to start', () => {
		const lanes = {
			7: { status: 'running' },
			5: { status: 'down' },
			1: { status: 'paused' }
		} as const;
		expect(problems(lanes, 'ok')).toEqual([{ text: 'Lane 5 is not betting.', lane: 5 }]);
	});

	it('lists nothing while the whole bot is off on purpose or all is well', () => {
		expect(problems({ 1: { status: 'down' }, 5: { status: 'down' } }, 'ok')).toEqual([]);
		expect(problems({ 1: { status: 'running' }, 5: { status: 'stale' } }, 'ok')).toEqual([]);
		expect(problems({}, undefined)).toEqual([]);
	});

	it('says when the bot host stops answering', () => {
		expect(problems({ 1: { status: 'running' } }, 'unavailable')).toEqual([{ text: AGENT_SILENT }]);
	});
});
