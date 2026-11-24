import { describe, expect, it } from 'vitest';
import type { ConfigKey } from './config';
import { keyIdFor, labelError, secretError, usedBy, withVaultChoices, type VaultKey } from './keys';

const row = (id: string, kind: VaultKey['kind']): VaultKey => ({
	id,
	label: id,
	kind,
	last4: '0000',
	owner_user_id: null,
	created_at: '2026-11-24T10:00:00+00:00',
	rotated_at: null,
	used_by_lanes: []
});

describe('key names', () => {
	it('slugs a label as the api does', () => {
		expect(keyIdFor('Friend A')).toBe('friend-a');
		expect(keyIdFor('  night__run 2 ')).toBe('night-run-2');
		expect(keyIdFor('x'.repeat(40))).toBe('x'.repeat(32));
		expect(keyIdFor('!!!')).toBeNull();
	});

	it('explains a bad label', () => {
		expect(labelError('Friend A')).toBeNull();
		expect(labelError('')).toBe('Use 1-64 characters.');
		expect(labelError(' x')).toBe('Use printable characters without spaces at either end.');
		expect(labelError('a\nb')).toBe('Use printable characters without spaces at either end.');
		expect(labelError('-')).toBe('Use at least one letter or digit.');
	});
});

describe('key secrets', () => {
	it('takes keys and proxy urls and never repeats them', () => {
		expect(secretError('key', 'wb_live_FRIEND_0001')).toBeNull();
		expect(secretError('proxy', 'socks5://u:p@10.0.0.9:1080')).toBeNull();
		for (const bad of ['short', 'has a space in it', 'x'.repeat(4097)]) {
			expect(secretError('key', bad)).toBe('Use 8-4096 printable characters with no spaces.');
		}
		const message = secretError('proxy', 'ftp://u:SECRET@host');
		expect(message).toBe('A proxy starts with http://, https:// or socks5://.');
		expect(message).not.toContain('SECRET');
	});
});

describe('key users and pickers', () => {
	it('says which lanes use a key', () => {
		expect(usedBy([1, 2])).toBe('lane 1, 2');
		expect(usedBy([])).toBe('not used');
	});

	it('offers each ref only the ids of its kind', () => {
		const fields = [
			{ key: 'key_ref', type: 'string' },
			{ key: 'proxy_ref', type: 'string', off_allowed: true },
			{ key: 'max_wins', type: 'integer' }
		] as ConfigKey[];
		const vault = [row('default', 'key'), row('friend-a', 'key'), row('friend-proxy', 'proxy')];
		const out = withVaultChoices(fields, vault);
		expect(out[0]?.choices).toEqual(['default', 'friend-a']);
		expect(out[1]?.choices).toEqual(['friend-proxy']);
		expect(out[2]).toBe(fields[2]);
	});
});
