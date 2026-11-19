import type { components, paths } from '#lib/api/schema';
import type { JobLine } from '#lib/live/protocol';
import { denialReason, type Me, type Perm } from '#lib/permissions';

export type Action = components['schemas']['ActionOut'];
export type ControlMenu = components['schemas']['ControlMenu'];
export type Job = components['schemas']['JobOut'];
export type ControlOut = components['schemas']['ControlOut'];
export type ControlPath = Extract<keyof paths, `/{service}/control/${string}`>;
export type ActionName = ControlPath extends `/{service}/control/${infer N}` ? N : never;
export type ActionBody = Record<string, string | number | Record<string, string>>;

export const BUSY_REASON = 'Wait for the running action to finish.';
export const MAX_JOB_LINES = 5000;

/** How a lane target reads in a sentence: `all` is "all lanes", 2 is "lane 2". */
export function targetLabel(target: unknown): string {
	return typeof target === 'number' ? `lane ${target}` : 'all lanes';
}

/**
 * The api's confirm text with its `{placeholders}` filled from the request body. `{target}`
 * defaults to all lanes, `{split}` to the current one, and `{branch}` reads as nothing when
 * no branch is given.
 */
export function fill(template: string, body: ActionBody, service: string): string {
	return template.replace(/\{(\w+)\}/g, (_, key: string) => {
		const value = body[key];
		if (key === 'service') return service;
		if (key === 'target') return targetLabel(value);
		if (key === 'branch') return typeof value === 'string' && value ? ` from branch ${value}` : '';
		if (key === 'split') return typeof value === 'string' ? value : 'current';
		return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
	});
}

/** A fresh idempotency key: one per confirmed press, reused only to retry that press. */
export function newKey(): string {
	return crypto.randomUUID();
}

/** Why the button for `action` is disabled, or null when it can be pressed. */
export function disabledReason(
	action: Action,
	me: Me | null,
	service: string,
	running: boolean
): string | null {
	return denialReason(me, action.perm as Perm, service) ?? (running ? BUSY_REASON : null);
}

/** `lines` with `line` added unless it was already seen (a resubscribe replays from 0). */
export function addLine(lines: JobLine[], line: JobLine): JobLine[] {
	const last = lines.at(-1);
	if (last !== undefined && line.seq <= last.seq) return lines;
	const next = [...lines, line];
	return next.length > MAX_JOB_LINES ? next.slice(-MAX_JOB_LINES) : next;
}
