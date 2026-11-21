import { describe, expect, it } from 'vitest';
import { epochLabel, parseEpoch } from './epochs';

describe('parseEpoch', () => {
	it('reads a positive id and ignores anything else', () => {
		expect(parseEpoch(new URLSearchParams('epoch=3'))).toBe(3);
		expect(parseEpoch(new URLSearchParams(''))).toBeNull();
		for (const bad of ['0', '-1', '2.5', 'x']) {
			expect(parseEpoch(new URLSearchParams({ epoch: bad }))).toBeNull();
		}
	});
});

describe('epochLabel', () => {
	const epoch = { epoch_id: 1, current: false, first_started_at: '2026-03-11T01:41:25Z' };

	it('spans the first to the last session', () => {
		const label = epochLabel({ ...epoch, last_started_at: '2026-11-21T18:00:00Z' });
		expect(label).toMatch(/2026 - .*2026$/);
		expect(label).toContain('11');
	});

	it('shows one date when the database holds a single day', () => {
		expect(epochLabel({ ...epoch, last_started_at: null })).not.toContain(' - ');
	});
});
