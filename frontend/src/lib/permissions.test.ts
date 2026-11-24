import { describe, expect, it } from 'vitest';
import { can, denialReason, type Me } from './permissions';

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
