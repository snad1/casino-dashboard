import { describe, expect, it } from 'vitest';
import { concerns, lengthLabel, newComment, type CommentTarget } from './comments';

const session: CommentTarget = { kind: 'session', epoch_id: 1, session_id: 7 };
const chart: CommentTarget = {
	kind: 'chart',
	from: '2026-01-05T10:00:00Z',
	to: '2026-01-05T11:00:00Z'
};
const event = (over: object) => ({
	kind: 'session' as const,
	epoch_id: 1,
	session_id: 7,
	bet_id: null,
	at: null,
	...over
});

describe('comment targets', () => {
	it('builds the new comment for each kind', () => {
		expect(newComment(session, 'hi', null)).toEqual({
			kind: 'session',
			epoch_id: 1,
			session_id: 7,
			body: 'hi'
		});
		expect(newComment({ kind: 'bet', epoch_id: 1, bet_id: 3 }, 'b', null)).toEqual({
			kind: 'bet',
			epoch_id: 1,
			bet_id: 3,
			body: 'b'
		});
		expect(newComment(chart, 'n', '2026-01-05T10:30:00Z')).toEqual({
			kind: 'chart',
			at: '2026-01-05T10:30:00Z',
			body: 'n'
		});
	});

	it('reloads only for changes to what it shows', () => {
		expect(concerns(session, event({}))).toBe(true);
		expect(concerns(session, event({ session_id: 8 }))).toBe(false);
		expect(concerns(session, event({ kind: 'bet', session_id: null, bet_id: 7 }))).toBe(false);
		const note = event({ kind: 'chart', epoch_id: null, session_id: null });
		expect(concerns(chart, { ...note, at: '2026-01-05T10:30:00Z' })).toBe(true);
		expect(concerns(chart, { ...note, at: '2026-01-05T12:30:00Z' })).toBe(false);
	});

	it('counts trimmed characters', () => {
		expect(lengthLabel('  abc  ')).toBe('3 of 2000');
	});
});
