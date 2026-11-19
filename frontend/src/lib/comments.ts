import type { components } from '#lib/api/schema';
import type { CommentEvent } from '#lib/live/protocol';

export const MAX_COMMENT = 2000;
export type CommentOut = components['schemas']['CommentOut'];
export type CommentIn = components['schemas']['CommentIn'];

export type CommentTarget =
	| { kind: 'session'; epoch_id: number; session_id: number }
	| { kind: 'bet'; epoch_id: number; bet_id: number }
	| { kind: 'chart'; from: string; to: string };

export type ListQuery =
	| { kind: 'session'; epoch_id: number; session_id: number }
	| { kind: 'bet'; epoch_id: number; bet_id: number }
	| { kind: 'chart'; from: string; to: string };

/** The list query for one target; the shape is the same as the target itself. */
export function listQuery(target: CommentTarget): ListQuery {
	return { ...target };
}

/** The new comment's target fields. A chart note needs the time it is pinned to. */
export function newComment(target: CommentTarget, body: string, at: string | null): CommentIn {
	if (target.kind === 'session') {
		return { kind: 'session', epoch_id: target.epoch_id, session_id: target.session_id, body };
	}
	if (target.kind === 'bet') {
		return { kind: 'bet', epoch_id: target.epoch_id, bet_id: target.bet_id, body };
	}
	return { kind: 'chart', at, body };
}

/** Whether a live change concerns what this target shows, so it should reload. */
export function concerns(target: CommentTarget, event: CommentEvent): boolean {
	if (event.kind !== target.kind) return false;
	if (target.kind === 'session') {
		return event.epoch_id === target.epoch_id && event.session_id === target.session_id;
	}
	if (target.kind === 'bet') {
		return event.epoch_id === target.epoch_id && event.bet_id === target.bet_id;
	}
	if (event.at === null) return false;
	const at = Date.parse(event.at);
	return at >= Date.parse(target.from) && at <= Date.parse(target.to);
}

export function lengthLabel(text: string): string {
	return `${text.trim().length} of ${MAX_COMMENT}`;
}
