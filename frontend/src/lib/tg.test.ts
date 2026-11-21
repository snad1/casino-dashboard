import { describe, expect, it } from 'vitest';
import type { Me } from './permissions';
import { betsCsvUrl, canSeeTgUser, sessionsCsvUrl, settingLabel, settingValue, tgHome } from './tg';

const me = (role: string, tg_uid: number | null = null, totp = true): Me => ({
	email: tg_uid === null ? 'a@b.test' : null,
	tg_uid,
	csrf_token: 't',
	totp_enabled: totp,
	roles: [{ role, service: null }]
});

describe('tgHome', () => {
	it('sends a Telegram account to its own pages and nobody else anywhere', () => {
		expect(tgHome(me('tg_user', 42))).toBe('/tg/42');
		expect(tgHome(me('admin'))).toBeNull();
		expect(tgHome(null)).toBeNull();
	});
});

describe('canSeeTgUser', () => {
	it('lets a Telegram account see only its own uid', () => {
		expect(canSeeTgUser(me('tg_user', 42), 42)).toBe(true);
		expect(canSeeTgUser(me('tg_user', 42), 43)).toBe(false);
	});

	it('lets user managers with 2FA see anyone, and nobody else', () => {
		expect(canSeeTgUser(me('admin'), 43)).toBe(true);
		expect(canSeeTgUser(me('admin', null, false), 43)).toBe(false);
		expect(canSeeTgUser(me('operator'), 43)).toBe(false);
		expect(canSeeTgUser(null, 43)).toBe(false);
	});
});

describe('downloads and settings', () => {
	it('builds fixed api paths for the CSV files', () => {
		expect(sessionsCsvUrl(42)).toBe('/api/tg/42/sessions.csv');
		expect(betsCsvUrl(42, 7, 3)).toBe('/api/tg/42/sessions/7/3/bets.csv');
	});

	it('labels known settings and reads empty ones as not set', () => {
		expect(settingLabel('max_loss')).toBe('Stop at loss');
		expect(settingLabel('milestone_bets')).toBe('Milestone bets');
		expect(settingValue(null)).toBe('Not set');
		expect(settingValue('')).toBe('Not set');
		expect(settingValue(0.5)).toBe('0.5');
		expect(settingValue({ a: 1 })).toBe('{"a":1}');
	});
});
