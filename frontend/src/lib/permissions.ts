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
	| 'alerts.manage';

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
	'alerts.manage'
];
const VIEWER: Perm[] = ['view', 'comment'];
const OPERATOR: Perm[] = [...VIEWER, 'control.basic', 'config.write', 'export', 'logs'];

const ROLE_PERMS: Record<string, Perm[]> = {
	owner: ALL,
	admin: ALL.filter((p) => p !== 'terminal'),
	operator: OPERATOR,
	viewer: VIEWER
};
const NEEDS_TWO_FACTOR = new Set(['owner', 'admin']);

/**
 * Whether the signed-in user may use `perm` on `service`, mirroring the api's role map. The
 * api decides; this only hides what would be refused. Owner and admin grants beyond `view`
 * count only once 2FA is on, as on the server.
 */
export function can(me: Me | null, perm: Perm, service: string | null = null): boolean {
	if (me === null) return false;
	return me.roles.some(({ role, service: scope }) => {
		if (scope !== null && scope !== service) return false;
		if (!(ROLE_PERMS[role] ?? []).includes(perm)) return false;
		return perm === 'view' || !NEEDS_TWO_FACTOR.has(role) || me.totp_enabled;
	});
}
