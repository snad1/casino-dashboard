import { describe, expect, it } from 'vitest';
import { isOwner, updateText } from './updates';

const state = (behind: number | null) => ({
	behind,
	head: behind === null ? null : 'abc1234',
	latest: behind === null ? null : 'def5678',
	auto_update: false
});

describe('the update notice', () => {
	it('names how many commits and both hashes', () => {
		expect(updateText(state(3))).toBe('Update available: 3 commits (abc1234 to def5678).');
		expect(updateText(state(1))).toBe('Update available: 1 commit (abc1234 to def5678).');
	});

	it('says up to date, or that it could not check', () => {
		expect(updateText(state(0))).toBe('Up to date (abc1234).');
		expect(updateText(state(null))).toBe("Couldn't check for updates.");
	});

	it('lets only a global owner switch auto-update', () => {
		expect(isOwner({ roles: [{ role: 'owner', service: null }] })).toBe(true);
		expect(isOwner({ roles: [{ role: 'admin', service: null }] })).toBe(false);
		expect(isOwner(null)).toBe(false);
	});
});
