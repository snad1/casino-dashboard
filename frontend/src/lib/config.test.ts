import { describe, expect, it } from 'vitest';
import {
	appliedSince,
	BASE,
	changesFrom,
	current,
	display,
	fieldKind,
	initialEdits,
	needsRestart,
	reloadMarks,
	type ConfigKey,
	type ConfigOut,
	type Preview
} from './config';

const key = (over: Partial<ConfigKey>): ConfigKey => ({
	key: 'k',
	type: 'number',
	off_allowed: false,
	live_reload: true,
	lane_overridable: true,
	...over
});
const CONFIG: ConfigOut = {
	keys: [
		key({ key: 'max_loss', off_allowed: true }),
		key({ key: 'multiplier', live_reload: false }),
		key({ key: 'game', type: 'string', choices: ['dice', 'limbo'], lane_overridable: false }),
		key({ key: 'recurring', type: 'boolean' }),
		key({ key: 'streak_delay_win', type: 'string', format: 'N:X' })
	],
	lane_count: 3,
	override_lanes: { min: 2, max: 10 },
	split_choices: ['none', 'budget'],
	lane_split: 'none',
	base: {
		max_loss: 0.02,
		multiplier: 2.01,
		game: 'dice',
		recurring: false,
		streak_delay_win: [3, 1.5]
	},
	lanes: { '2': { multiplier: 3 } }
};

describe('fields from the schema', () => {
	it('picks an input per key type', () => {
		expect(CONFIG.keys.map(fieldKind)).toEqual(['number', 'number', 'select', 'boolean', 'text']);
	});

	it('shows saved values as form text, lanes falling back to the base', () => {
		expect(display(null)).toBe('');
		expect(display([3, 1.5])).toBe('3:1.5');
		expect(current(CONFIG, BASE, 'streak_delay_win')).toBe('3:1.5');
		expect(current(CONFIG, '2', 'multiplier')).toBe('3');
		expect(current(CONFIG, '3', 'multiplier')).toBe('2.01');
		expect(current(CONFIG, BASE, 'lane_split')).toBe('none');
	});

	it('offers only overridable keys on a lane tab', () => {
		expect(Object.keys(initialEdits(CONFIG, '2'))).not.toContain('game');
		expect(initialEdits(CONFIG, '2').multiplier).toEqual({ value: '3', useBase: false });
		expect(initialEdits(CONFIG, '3').multiplier).toEqual({ value: '2.01', useBase: true });
		expect(Object.keys(initialEdits(CONFIG, BASE))).toContain('lane_split');
	});
});

describe('changesFrom', () => {
	it('sends only what differs, with off and default spelled out', () => {
		const edits = {
			[BASE]: {
				...initialEdits(CONFIG, BASE),
				max_loss: { value: '', useBase: true },
				recurring: { value: 'true', useBase: true },
				lane_split: { value: 'budget', useBase: true }
			},
			'2': { ...initialEdits(CONFIG, '2'), multiplier: { value: '3', useBase: true } },
			'3': { ...initialEdits(CONFIG, '3'), multiplier: { value: '4 ', useBase: false } }
		};
		expect(changesFrom(CONFIG, edits)).toEqual([
			{ key: 'max_loss', value: 'off', lane: null },
			{ key: 'recurring', value: 'true', lane: null },
			{ key: 'lane_split', value: 'budget', lane: null },
			{ key: 'multiplier', value: 'default', lane: 2 },
			{ key: 'multiplier', value: '4', lane: 3 }
		]);
	});

	it('is empty when nothing changed', () => {
		const edits = { [BASE]: initialEdits(CONFIG, BASE), '2': initialEdits(CONFIG, '2') };
		expect(changesFrom(CONFIG, edits)).toEqual([]);
	});
});

describe('preview and reload', () => {
	const row = (apply: 'live' | 'restart') => ({ key: 'k', old: 1, new: 2, apply, removed: false });

	it('flags a restart when any row needs one', () => {
		const preview: Preview = { base: [row('live')], lanes: {}, warnings: [] };
		expect(needsRestart(preview)).toBe(false);
		expect(needsRestart({ ...preview, lanes: { '2': [row('restart')] } })).toBe(true);
	});

	it('reports only reloads that came after the save', () => {
		const before = { 1: { last_reload: { ts_ms: 5, keys: ['old'] } }, 2: {} };
		const marks = reloadMarks(before);
		expect(appliedSince(before, marks)).toEqual([]);
		const after = { ...before, 1: { last_reload: { ts_ms: 9, keys: ['max_loss'] } } };
		expect(appliedSince(after, marks)).toEqual([{ lane: 1, keys: ['max_loss'] }]);
	});
});
