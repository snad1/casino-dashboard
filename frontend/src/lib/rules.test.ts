import { describe, expect, it } from 'vitest';
import {
	BAD_JSON,
	NOT_JSON,
	blankRule,
	describeRule,
	parseRules,
	rulesText,
	takesValue,
	type RuleOptions
} from './rules';

const OPTIONS: RuleOptions = {
	cond_types: ['sequence', 'profit', 'bet'],
	seq_modes: [
		{ key: 'every', label: 'Every N (total count)' },
		{ key: 'every_streak', label: 'Every N in streak (N, 2N, 3N...)' }
	],
	seq_triggers: ['win', 'loss', 'bet'],
	profit_fields: ['profit', 'loss', 'balance'],
	bet_fields: ['amount', 'number', 'winchance', 'payout'],
	ops: [
		{ key: 'gte', label: '>=' },
		{ key: 'lt', label: '<' }
	],
	actions: [
		{ key: 'reset_amount', label: 'Reset bet amount', value: false },
		{ key: 'increase_amount', label: 'Increase amount by %', value: true },
		{ key: 'stop', label: 'Stop betting', value: false }
	]
};
// What the bot's wizard saves for one rule (StrategyRule.to_dict).
const SAVED =
	'[{"cond_type": "sequence", "cond_field": "", "cond_mode": "every_streak", "cond_value": 3.0,' +
	' "cond_trigger": "loss", "action": "increase_amount", "action_value": 100.0,' +
	' "description": "Every streak of 3 loss(s) → Increase amount by % 100.0"}]';

describe('rules text', () => {
	it("reads the bot's saved rules and writes them back in its key order", () => {
		const { rules, error } = parseRules(SAVED);
		expect(error).toBeNull();
		expect(rules[0]).toMatchObject({ cond_mode: 'every_streak', cond_value: 3, action_value: 100 });
		const text = rulesText(rules, OPTIONS);
		expect(Object.keys(JSON.parse(text)[0])).toEqual([
			'cond_type',
			'cond_field',
			'cond_mode',
			'cond_value',
			'cond_trigger',
			'action',
			'action_value',
			'description'
		]);
		expect(text).not.toContain('\n');
		expect(parseRules(text).rules).toEqual(parseRules(text).rules);
	});

	it('is empty with no rules, and says why text cannot be built', () => {
		expect(rulesText([])).toBe('');
		expect(parseRules('')).toEqual({ rules: [], error: null });
		expect(parseRules('[{"cond_type": ').error).toBe(BAD_JSON);
		expect(parseRules('[1, 2]').error).toBe(BAD_JSON);
		expect(parseRules('onEvery3loseReset').error).toBe(NOT_JSON);
	});
});

describe('rule words', () => {
	it("describes each condition type as the bot's describe_rule does", () => {
		const [seq] = parseRules(SAVED).rules;
		if (!seq) throw new Error('no rule');
		expect(describeRule(seq, OPTIONS)).toBe('Every streak of 3 loss(s) → Increase amount by % 100');
		const profit = { ...blankRule('profit', OPTIONS), cond_value: 0.00000001, action: 'stop' };
		expect(describeRule(profit, OPTIONS)).toBe('On profit >= 0.00000001 → Stop betting');
		const bet = { ...blankRule('bet', OPTIONS), cond_mode: 'lt', cond_value: 2 };
		expect(describeRule(bet, OPTIONS)).toBe('On bet amount < 2 → Reset bet amount');
	});

	it('starts a rule with the first choice of each menu and keeps the action on retype', () => {
		expect(blankRule('sequence', OPTIONS)).toMatchObject({
			cond_mode: 'every',
			cond_value: 1,
			cond_trigger: 'win',
			action: 'reset_amount'
		});
		const kept = blankRule('bet', OPTIONS, {
			...blankRule('sequence', OPTIONS),
			action: 'increase_amount',
			action_value: 50
		});
		expect(kept).toMatchObject({
			cond_field: 'amount',
			action: 'increase_amount',
			action_value: 50
		});
	});

	it('knows which actions take a value', () => {
		expect(takesValue('increase_amount', OPTIONS)).toBe(true);
		expect(takesValue('stop', OPTIONS)).toBe(false);
	});
});
