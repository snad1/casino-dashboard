import type { components } from '#lib/api/schema';

export type UpdateState = components['schemas']['UpdateOut'];

const commits = (n: number) => `${n} commit${n === 1 ? '' : 's'}`;

/** What the Updates card says about the bot's checkout against its upstream. */
export function updateText(state: UpdateState): string {
	if (state.behind === null) return "Couldn't check for updates.";
	if (state.behind === 0) return `Up to date (${state.head}).`;
	return `Update available: ${commits(state.behind)} (${state.head} to ${state.latest}).`;
}

/** Whether `me` is the owner, the only role that may switch auto-update. */
export function isOwner(
	me: { roles: { role: string; service?: string | null }[] } | null
): boolean {
	return me?.roles.some((r) => r.role === 'owner' && !r.service) ?? false;
}
