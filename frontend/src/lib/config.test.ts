import { describe, expect, it } from 'vitest';
import {
	appliedSince,
	BASE,
	costLabel,
	changesFrom,
	current,
	display,
	fieldKind,
	initialEdits,
	laneScopes,
	needsRestart,
	restartPlan,
	parsePasted,
	pendingText,
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
	owner_only: false,
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
	lane_ids: [1, 2, 3],
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
	lanes: { '2': { multiplier: 3 } },
	pending: {},
	rollout: {}
};

describe('fields from the schema', () => {
	it('picks an input per key type', () => {
		expect(CONFIG.keys.map(fieldKind)).toEqual(['number', 'number', 'select', 'boolean', 'text']);
		expect(fieldKind(key({ type: 'string', format: 'rules' }))).toBe('long');
	});

	it('sends a rules textarea as one line', () => {
		const config = { ...CONFIG, keys: [key({ key: 'custom_rules_text', type: 'string' })] };
		const edits = { [BASE]: { custom_rules_text: { value: '[\n  {"n": 3}\n]', useBase: false } } };
		expect(changesFrom(config, edits)).toEqual([
			{ key: 'custom_rules_text', value: '[ {"n": 3} ]', lane: null }
		]);
	});

	it('shows saved values as form text, lanes falling back to the base', () => {
		expect(display(null)).toBe('');
		expect(display([3, 1.5])).toBe('3:1.5');
		expect(display(1e-8)).toBe('0.00000001');
		expect(display([3, 1e-8])).toBe('3:0.00000001');
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

	it('names what each change costs', () => {
		expect(costLabel('live', BASE)).toBe('Applies live');
		expect(costLabel('live', '3')).toBe('Applies live');
		expect(costLabel('restart', '3')).toBe('Restarts lane 3 at its session end');
		expect(costLabel('restart', BASE)).toBe('Restarts each lane that uses it at its session end');
	});

	it('restarts only the lanes a change affects, never the whole service', () => {
		const config = { lane_ids: [1, 5, 6, 7], lanes: { '6': { balance_goal: 9 } } };
		const lanes = { '7': [row('restart')], '5': [row('live')] };
		expect(restartPlan({ base: [row('live')], lanes, warnings: [] }, config)).toEqual([7]);
		const base = [{ ...row('restart'), key: 'balance_goal' }];
		expect(restartPlan({ base, lanes: {}, warnings: [] }, config)).toEqual([1, 5, 7]);
		expect(restartPlan({ base, lanes, warnings: [] }, config)).toEqual([1, 5, 7]);
		expect(restartPlan({ base: [row('live')], lanes: {}, warnings: [] }, config)).toEqual([]);
	});

	it('builds lane tabs from the real lane ids', () => {
		expect(laneScopes([7, 1, 5, 6], { min: 2, max: 10 })).toEqual(['5', '6', '7']);
		expect(laneScopes([1], { min: 2, max: 10 })).toEqual([]);
		expect(laneScopes([2, 12], { min: 2, max: 10 })).toEqual(['2']);
	});

	it('reports only reloads that came after the save', () => {
		const before = { 1: { last_reload: { ts_ms: 5, keys: ['old'] } }, 2: {} };
		const marks = reloadMarks(before);
		expect(appliedSince(before, marks)).toEqual([]);
		const after = { ...before, 1: { last_reload: { ts_ms: 9, keys: ['max_loss'] } } };
		expect(appliedSince(after, marks)).toEqual([{ lane: 1, keys: ['max_loss'] }]);
	});
});

describe('pendingText', () => {
	it('lists each waiting key as running -> saved', () => {
		expect(
			pendingText([
				{ key: 'multiplier', running: 3, saved: 4 },
				{ key: 'streak_delay_win', running: [3, 1.5], saved: null }
			])
		).toBe('multiplier 3 -> 4, streak_delay_win 3:1.5 -> ');
	});
});

describe('parsePasted', () => {
	const SHOW = [
		'┏━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓',
		'┃ Field     ┃ Value       ┃ Description ┃',
		'│ api_key   │ eyJ0eXAi...MWu8 (SET) │ API bearer token (set via WOLFBET_API_KEY env │',
		'│           │             │ var)        │',
		'│ currency  │ usdt        │ Betting currency │',
		'│ multiplier │ 2          │ Payout multiplier │',
		'│ max_loss  │ off         │ Stop when loss ≥ this │',
		'│ recurring │ on          │ Auto-restart cycle │',
		'│ game      │ limbo       │ Game        │',
		'│ custom_rules_text │ [{"cond_type": "sequence", │ Rule-Based custom rules │',
		'│           │ "cond_mode":... │             │',
		'└───────────┴─────────────┴─────────────┘',
		'',
		'Lanes: 2  split: budget  (every lane uses the full base bet, no lane budget)'
	].join('\n');

	it('fills the editable keys from a config-show table', () => {
		const out = parsePasted(SHOW, CONFIG.keys);
		expect(out.values).toEqual({
			multiplier: '2',
			max_loss: '',
			recurring: 'true',
			game: 'limbo',
			lane_split: 'budget'
		});
		expect(out.skipped).toEqual([
			{ key: 'lanes', reason: 'set it on the Lanes page' },
			{ key: 'api_key', reason: "secret: stays in the bot's .env" },
			{ key: 'currency', reason: 'not editable here' },
			{ key: 'custom_rules_text', reason: 'value is cut off; type it in' }
		]);
	});

	it('drops the live note after a bumped base bet', () => {
		const keys = [key({ key: 'base_bet' })];
		const row = '│ base_bet │ 0.00000388  (live 0.00000505, +117 bumps) │ Base bet amount │';
		expect(parsePasted(row, keys).values).toEqual({ base_bet: '0.00000388' });
	});

	it('clears a direct proxy and skips a shown one, whose password is masked', () => {
		const keys = [key({ key: 'proxy', type: 'string', format: 'url', off_allowed: true })];
		expect(parsePasted('│ proxy │ direct │ Proxy URL │', keys).values).toEqual({ proxy: '' });
		const shown = parsePasted('proxy=socks5://host:1080', keys);
		expect(shown).toEqual({
			values: {},
			skipped: [{ key: 'proxy', reason: 'shown without its password; type it in' }]
		});
	});

	it('reads key=value and key: value lines', () => {
		const out = parsePasted(
			'multiplier=2.5\nrecurring: no\nmax_loss = 0.01\napi_key=x',
			CONFIG.keys
		);
		expect(out.values).toEqual({ multiplier: '2.5', recurring: 'false', max_loss: '0.01' });
		expect(out.skipped.map((s) => s.key)).toEqual(['api_key']);
	});

	it('says where the keys the form leaves out are set', () => {
		expect(
			parsePasted('lanes=2\nbet_value=50.49\nstrategy=Martingale', CONFIG.keys).skipped
		).toEqual([
			{ key: 'lanes', reason: 'set it on the Lanes page' },
			{ key: 'bet_value', reason: 'worked out from multiplier and rule' },
			{ key: 'strategy', reason: 'set by strategy_key' }
		]);
	});
});
