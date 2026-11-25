import type { components } from '#lib/api/schema';

export type Me = components['schemas']['Me'];
export type Perm =
	| 'view'
	| 'comment'
	| 'control.basic'
	| 'control.destructive'
	| 'config.write'
	| 'export'
	| 'logs'
	| 'terminal'
	| 'users.manage'
	| 'audit.read'
	| 'alerts.manage'
	| 'keys.manage'
	| 'lanes.create'
	| 'keys.own'
	| 'comments.moderate';

const ALL: Perm[] = [
	'view',
	'comment',
	'control.basic',
	'control.destructive',
	'config.write',
	'export',
	'logs',
	'terminal',
	'users.manage',
	'audit.read',
	'alerts.manage',
	'keys.manage',
	'lanes.create',
	'keys.own',
	'comments.moderate'
];
const VIEWER: Perm[] = ['view', 'comment'];
const OPERATOR: Perm[] = [...VIEWER, 'control.basic', 'config.write', 'export', 'logs'];

const ROLE_PERMS: Record<string, Perm[]> = {
	owner: ALL,
	admin: ALL.filter((p) => p !== 'terminal'),
	operator: OPERATOR,
	viewer: VIEWER,
	// A friend who creates and runs their own lane with their own keys, nothing service-wide.
	lane_manager: ['lanes.create', 'keys.own'],
	// A Telegram account reaches only its own bot user, through the `/tg/<uid>` routes.
	tg_user: []
};
const NEEDS_TWO_FACTOR = new Set(['owner', 'admin']);

/** Each role in words, beside `ROLE_PERMS` so a change to one is a change to the other. */
export const ROLE_INFO = {
	viewer: {
		name: 'Viewer',
		help: 'Sees the live view, sessions and analytics, and can comment. Changes nothing.'
	},
	operator: {
		name: 'Operator',
		help: 'Everything a viewer does, plus start, stop and restart lanes, change config, export and read logs. No keys, users, reset or terminal.'
	},
	admin: {
		name: 'Admin',
		help: 'Everything except the web terminal: keys, users, alerts and reset too. Needs two-factor sign-in.'
	},
	lane_manager: {
		name: 'Lane manager',
		help: 'Adds lanes of their own, up to their lane quota, saves their own casino keys, and sees and runs only their own lanes.'
	},
	owner: {
		name: 'Owner',
		help: 'Everything, the web terminal included. Only an owner can make someone owner.'
	}
} as const;

export const ON_SOME_LANES =
	'A viewer or operator given lanes sees and acts on those lanes only; nothing service-wide.';

/**
 * Whether the signed-in user may use `perm` on `service`, mirroring the api's role map. The
 * api decides; this only hides what would be refused. Owner and admin grants beyond `view`
 * count only once 2FA is on, as on the server. A role on one lane opens nothing service-wide.
 */
export function can(me: Me | null, perm: Perm, service: string | null = null): boolean {
	if (me === null) return false;
	return me.roles.some(({ role, service: scope, lane }) => {
		if (lane !== null && lane !== undefined) return false;
		if (scope !== null && scope !== service) return false;
		if (!(ROLE_PERMS[role] ?? []).includes(perm)) return false;
		return perm === 'view' || !NEEDS_TWO_FACTOR.has(role) || me.totp_enabled;
	});
}

/**
 * The lanes of `service` where the signed-in user may use `perm`, mirroring the api's lane
 * scope: null when a service-wide role grants it, else the lanes whose lane role does
 * (empty when none). Lane roles are viewer or operator, which need no 2FA.
 */
export function laneScope(me: Me | null, perm: Perm, service: string): number[] | null {
	if (can(me, perm, service)) return null;
	const lanes = new Set<number>();
	for (const { role, service: scope, lane } of me?.roles ?? []) {
		if (lane === null || lane === undefined || scope !== service) continue;
		if ((ROLE_PERMS[role] ?? []).includes(perm)) lanes.add(lane);
	}
	return [...lanes].sort((a, b) => a - b);
}

const ROLE_NAMES: Partial<Record<Perm, string>> = {
	'control.basic': 'operator',
	'config.write': 'operator',
	export: 'operator',
	'control.destructive': 'admin'
};

/**
 * Why the signed-in user may not use `perm` on `service`, as text for a disabled control;
 * null when they may. A role that would grant it once 2FA is on asks for 2FA instead.
 */
export function denialReason(
	me: Me | null,
	perm: Perm,
	service: string | null = null
): string | null {
	if (can(me, perm, service)) return null;
	const granting = (me?.roles ?? []).some(
		({ role, service: scope, lane }) =>
			(lane === null || lane === undefined) &&
			(scope === null || scope === service) &&
			(ROLE_PERMS[role] ?? []).includes(perm)
	);
	if (granting) return 'Turn on two-factor authentication to use this.';
	return `Needs ${ROLE_NAMES[perm] ?? 'a different'} role`;
}
