import type { components } from '#lib/api/schema';
import { plainNumber } from '#lib/format';

export type RuleOptions = components['schemas']['RuleOptions'];

/** One Rule-Based rule in the bot's saved shape (`StrategyRule.to_dict`). */
export interface Rule {
	cond_type: string;
	cond_field: string;
	cond_mode: string;
	cond_value: number;
	cond_trigger: string;
	action: string;
	action_value: number;
	description: string;
}

export const BAD_JSON = 'Not valid rules JSON. Fix it on the JSON tab, or clear it.';
export const NOT_JSON = 'These rules are not JSON, so the builder cannot show them.';

const KEYS: Array<keyof Rule> = [
	'cond_type',
	'cond_field',
	'cond_mode',
	'cond_value',
	'cond_trigger',
	'action',
	'action_value',
	'description'
];
const SEQ_WORDS: Record<string, string> = {
	every: 'Every',
	every_streak: 'Every streak of',
	first_streak: 'First streak of',
	streak_above: 'Streak above',
	streak_below: 'Streak below'
};

const num = (value: unknown) => {
	const n = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(n) ? n : 0;
};
const str = (value: unknown) => (typeof value === 'string' ? value : '');

/**
 * The rules in a saved `custom_rules_text`. Empty text is no rules; text the bot reads as
 * `onEvery...` lines is not JSON, so the builder leaves it to the JSON tab; broken JSON is an
 * error that blocks Preview.
 */
export function parseRules(text: string): { rules: Rule[]; error: string | null } {
	const trimmed = text.trim();
	if (!trimmed) return { rules: [], error: null };
	if (!trimmed.startsWith('[')) return { rules: [], error: NOT_JSON };
	let data: unknown;
	try {
		data = JSON.parse(trimmed);
	} catch {
		return { rules: [], error: BAD_JSON };
	}
	if (!Array.isArray(data) || !data.every((d) => typeof d === 'object' && d !== null)) {
		return { rules: [], error: BAD_JSON };
	}
	const rules = (data as Array<Record<string, unknown>>).map((d) => ({
		cond_type: str(d.cond_type),
		cond_field: str(d.cond_field),
		cond_mode: str(d.cond_mode),
		cond_value: num(d.cond_value),
		cond_trigger: str(d.cond_trigger),
		action: str(d.action),
		action_value: num(d.action_value),
		description: str(d.description)
	}));
	return { rules, error: null };
}

/** The rules as the bot saves them: one JSON line, keys in its order; no rules is empty. */
export function rulesText(rules: Rule[], options: RuleOptions | null = null): string {
	if (rules.length === 0) return '';
	return JSON.stringify(
		rules.map((rule) => {
			const described = { ...rule, description: options ? describeRule(rule, options) : '' };
			return Object.fromEntries(KEYS.map((key) => [key, described[key]]));
		})
	);
}

/** A rule in words, as the bot's `describe_rule` puts it. */
export function describeRule(rule: Rule, options: RuleOptions): string {
	const op = options.ops.find((o) => o.key === rule.cond_mode)?.label ?? rule.cond_mode;
	let condition = '?';
	if (rule.cond_type === 'sequence') {
		const mode = SEQ_WORDS[rule.cond_mode] ?? rule.cond_mode;
		condition = `${mode} ${Math.trunc(rule.cond_value)} ${rule.cond_trigger}(s)`;
	} else if (rule.cond_type === 'profit') {
		condition = `On ${rule.cond_field} ${op} ${plainNumber(rule.cond_value)}`;
	} else if (rule.cond_type === 'bet') {
		condition = `On bet ${rule.cond_field} ${op} ${plainNumber(rule.cond_value)}`;
	}
	const action = options.actions.find((a) => a.key === rule.action);
	const label = action?.label ?? rule.action;
	const value = action?.value && rule.action_value ? ` ${plainNumber(rule.action_value)}` : '';
	return `${condition} → ${label}${value}`;
}

/** A new rule of `type` with the first choice of every menu; `keep` carries its action over. */
export function blankRule(type: string, options: RuleOptions, keep?: Rule): Rule {
	const rule: Rule = {
		cond_type: type,
		cond_field: '',
		cond_mode: '',
		cond_value: 0,
		cond_trigger: '',
		action: keep?.action ?? options.actions[0]?.key ?? '',
		action_value: keep?.action_value ?? 0,
		description: ''
	};
	if (type === 'sequence') {
		rule.cond_mode = options.seq_modes[0]?.key ?? '';
		rule.cond_value = 1;
		rule.cond_trigger = options.seq_triggers[0] ?? '';
	} else {
		rule.cond_field = (type === 'profit' ? options.profit_fields : options.bet_fields)[0] ?? '';
		rule.cond_mode = options.ops[0]?.key ?? '';
	}
	return rule;
}

/** Whether `action` takes a value (reset, switch and stop do not). */
export function takesValue(action: string, options: RuleOptions): boolean {
	return options.actions.find((a) => a.key === action)?.value ?? true;
}
