import { describe, expect, it } from 'vitest';
import { can, type Me } from './permissions';

function me(role: string, service: string | null = null, totp = true): Me {
	return { email: 'x@y.test', csrf_token: 't', totp_enabled: totp, roles: [{ role, service }] };
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
	});

	it('limits a service role to that service', () => {
		expect(can(me('operator', 'wolfbet'), 'view', 'wolfbet')).toBe(true);
		expect(can(me('operator', 'wolfbet'), 'view', 'goated')).toBe(false);
	});

	it('lets owners and admins only look until 2FA is on', () => {
		expect(can(me('admin', null, false), 'view')).toBe(true);
		expect(can(me('admin', null, false), 'users.manage')).toBe(false);
		expect(can(null, 'view')).toBe(false);
	});
});
