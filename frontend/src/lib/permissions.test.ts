import { describe, expect, it } from 'vitest';
import { can, denialReason, keyScope, laneScope, ROLE_INFO, type Me } from './permissions';

function me(role: string, service: string | null = null, totp = true): Me {
	return {
		email: 'x@y.test',
		tg_uid: null,
		csrf_token: 't',
		totp_enabled: totp,
		roles: [{ role, service }]
	};
}

describe('can', () => {
	it('follows the server role map', () => {
		expect(can(me('viewer'), 'view')).toBe(true);
		expect(can(me('viewer'), 'control.basic')).toBe(false);
		expect(can(me('operator'), 'control.basic')).toBe(true);
		expect(can(me('operator'), 'users.manage')).toBe(false);
		expect(can(me('admin'), 'users.manage')).toBe(true);
		expect(can(me('admin'), 'terminal')).toBe(false);
		expect(can(me('owner'), 'terminal')).toBe(true);
		expect(can(me('admin'), 'comments.moderate')).toBe(true);
		expect(can(me('viewer'), 'comments.moderate')).toBe(false);
	});

	it('gives a Telegram account nothing on any service', () => {
		for (const perm of ['view', 'comment', 'control.basic', 'users.manage'] as const) {
			expect(can(me('tg_user'), perm, 'wolfbet')).toBe(false);
			expect(can(me('tg_user'), perm, 'wolfbet-tg')).toBe(false);
		}
	});

	it('limits a service role to that service', () => {
		expect(can(me('operator', 'wolfbet'), 'view', 'wolfbet')).toBe(true);
		expect(can(me('operator', 'wolfbet'), 'view', 'goated')).toBe(false);
	});

	it('opens nothing service-wide for a role on one lane', () => {
		const friend: Me = {
			...me('viewer'),
			roles: [{ role: 'viewer', service: 'wolfbet', lane: 3 }]
		};
		expect(can(friend, 'view', 'wolfbet')).toBe(false);
		expect(can(friend, 'view')).toBe(false);
		const both: Me = {
			...me('viewer'),
			roles: [
				{ role: 'viewer', service: 'wolfbet', lane: null },
				{ role: 'operator', service: 'wolfbet', lane: 3 }
			]
		};
		expect(can(both, 'view', 'wolfbet')).toBe(true);
		expect(can(both, 'control.basic', 'wolfbet')).toBe(false);
	});

	it('lets a lane manager create a lane and use own keys, nothing else', () => {
		const friend = me('lane_manager', 'wolfbet', false);
		expect(can(friend, 'lanes.create', 'wolfbet')).toBe(true);
		expect(can(friend, 'keys.own', 'wolfbet')).toBe(true);
		for (const perm of [
			'view',
			'control.basic',
			'config.write',
			'keys.manage',
			'terminal'
		] as const) {
			expect(can(friend, perm, 'wolfbet')).toBe(false);
		}
	});

	it('lets owners and admins only look until 2FA is on', () => {
		expect(can(me('admin', null, false), 'view')).toBe(true);
		expect(can(me('admin', null, false), 'users.manage')).toBe(false);
		expect(can(null, 'view')).toBe(false);
	});
});

describe('denialReason', () => {
	it('names the role a control needs', () => {
		expect(denialReason(me('viewer'), 'control.basic')).toBe('Needs operator role');
		expect(denialReason(me('operator'), 'control.destructive')).toBe('Needs admin role');
		expect(denialReason(null, 'config.write')).toBe('Needs operator role');
	});

	it('asks for 2FA when the role would grant it', () => {
		expect(denialReason(me('admin', null, false), 'control.destructive')).toBe(
			'Turn on two-factor authentication to use this.'
		);
	});

	it('is null when allowed', () => {
		expect(denialReason(me('operator'), 'control.basic')).toBeNull();
		expect(denialReason(me('admin'), 'control.destructive', 'wolfbet')).toBeNull();
	});
});

describe('laneScope', () => {
	const laneUser = (roles: Me['roles'], totp = false): Me => ({
		email: 'friend@y.test',
		tg_uid: null,
		csrf_token: 't',
		totp_enabled: totp,
		roles
	});

	it('is every lane for a service role and the granted lanes otherwise', () => {
		expect(laneScope(me('viewer'), 'view', 'wolfbet')).toBeNull();
		expect(laneScope(me('viewer', 'wolfbet'), 'view', 'wolfbet')).toBeNull();
		const friend = laneUser([
			{ role: 'viewer', service: 'wolfbet', lane: 12 },
			{ role: 'operator', service: 'wolfbet', lane: 3 },
			{ role: 'viewer', service: 'other', lane: 5 }
		]);
		expect(laneScope(friend, 'view', 'wolfbet')).toEqual([3, 12]);
		expect(laneScope(friend, 'control.basic', 'wolfbet')).toEqual([3]);
		expect(laneScope(friend, 'users.manage', 'wolfbet')).toEqual([]);
		expect(
			laneScope(
				laneUser([{ role: 'lane_manager', service: 'wolfbet', lane: null }]),
				'view',
				'wolfbet'
			)
		).toEqual([]);
		expect(laneScope(null, 'view', 'wolfbet')).toEqual([]);
	});

	it('treats a key role as its lanes, never as the whole service', () => {
		const friend = laneUser([
			{ role: 'viewer', service: 'wolfbet', lane: 8, key_ref: 'lakai' },
			{ role: 'viewer', service: 'wolfbet', lane: 9, key_ref: 'lakai' }
		]);
		expect(can(friend, 'view', 'wolfbet')).toBe(false);
		expect(laneScope(friend, 'view', 'wolfbet')).toEqual([8, 9]);
		expect(keyScope(friend, 'view', 'wolfbet')).toEqual(['lakai']);
		const idle = laneUser([{ role: 'viewer', service: 'wolfbet', lane: null, key_ref: 'lakai' }]);
		expect(can(idle, 'view', 'wolfbet')).toBe(false);
		expect(laneScope(idle, 'view', 'wolfbet')).toEqual([]);
		expect(keyScope(idle, 'view', 'wolfbet')).toEqual(['lakai']);
		expect(keyScope(me('viewer'), 'view', 'wolfbet')).toBeNull();
		expect(
			keyScope(laneUser([{ role: 'viewer', service: 'wolfbet', lane: 3 }]), 'view', 'wolfbet')
		).toBeNull();
	});

	it('keeps the denial text in step with can for a lane role', () => {
		const friend = laneUser([{ role: 'operator', service: 'wolfbet', lane: 3 }], true);
		expect(can(friend, 'control.basic', 'wolfbet')).toBe(false);
		expect(denialReason(friend, 'control.basic', 'wolfbet')).toBe('Needs operator role');
	});
});

describe('role explanations', () => {
	it('explain every role the Users page offers, and the owner', () => {
		for (const role of ['viewer', 'operator', 'admin', 'lane_manager', 'owner'] as const) {
			expect(ROLE_INFO[role].name.length).toBeGreaterThan(0);
			expect(ROLE_INFO[role].help.length).toBeGreaterThan(20);
		}
	});

	it('match what each role may do', () => {
		expect(can(me('operator'), 'config.write')).toBe(true);
		expect(ROLE_INFO.operator.help).toContain('change config');
		expect(can(me('viewer'), 'config.write')).toBe(false);
		expect(ROLE_INFO.viewer.help).toContain('Changes nothing');
		expect(can(me('admin'), 'terminal')).toBe(false);
		expect(ROLE_INFO.admin.help).toContain('except the web terminal');
	});
});
