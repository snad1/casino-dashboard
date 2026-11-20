import type { components } from '#lib/api/schema';
import { formatDuration } from '#lib/analytics';

export type Rule = components['schemas']['RuleOut'];
export type RuleBody = components['schemas']['RuleIn'];
export type Kind = RuleBody['kind'];
export type Channels = components['schemas']['ChannelsOut'];
export type AlertEvent = components['schemas']['EventOut'];

export const CUSTOM = 'custom';

type Option = { value: string; label: string };
export type ValueKey =
	'period' | 'mode' | 'amount' | 'at' | 'pct' | 'for_s' | 'bps' | 'seconds' | 'pattern';
export type ManyKey = 'statuses' | 'reasons';
export type FieldSpec =
	| {
			key: ValueKey;
			label: string;
			type: 'choice';
			options: Option[];
			custom?: boolean;
			optional?: string;
	  }
	| { key: ManyKey; label: string; type: 'many'; options: Option[] }
	| { key: ValueKey; label: string; type: 'text'; hint: string };

const seconds = (values: number[]): Option[] =>
	values.map((v) => ({ value: String(v), label: v === 0 ? 'None' : formatDuration(v) }));
const plain = (values: number[], suffix = ''): Option[] =>
	values.map((v) => ({ value: String(v), label: `${v}${suffix}` }));

const AMOUNTS = plain([0.00001, 0.0001, 0.001, 0.01, 0.1, 1]);
const PERIOD: FieldSpec = {
	key: 'period',
	label: 'Period',
	type: 'choice',
	options: [
		{ value: 'session', label: 'Session' },
		{ value: 'run', label: 'Run' },
		{ value: 'day', label: 'Day (all lanes)' }
	]
};
const FOR_S: FieldSpec = {
	key: 'for_s',
	label: 'For at least',
	type: 'choice',
	options: seconds([30, 60, 120, 300, 600, 900, 1800, 3600]),
	custom: true
};

/** The bot's stop reasons, as the api accepts them. */
export const STOP_REASONS = [
	'Profit target reached',
	'Max loss hit',
	'Max bets reached',
	'Max wins reached',
	'Balance floor hit',
	'Balance goal reached',
	'Loss streak reset',
	'Lane budget exceeded',
	'Insufficient balance',
	'Recovery failed'
];

export const COOLDOWNS = seconds([0, 60, 300, 600, 1800, 3600, 21600, 86400]);

export const KINDS: Record<Kind, { label: string; fields: FieldSpec[] }> = {
	profit: {
		label: 'Profit',
		fields: [
			PERIOD,
			{
				key: 'mode',
				label: 'Alert',
				type: 'choice',
				options: [
					{ value: 'at', label: 'Once it reaches' },
					{ value: 'step', label: 'At every step of' }
				]
			},
			{ key: 'amount', label: 'Amount', type: 'choice', options: AMOUNTS, custom: true }
		]
	},
	loss: {
		label: 'Loss',
		fields: [
			PERIOD,
			{ key: 'at', label: 'Loss of', type: 'choice', options: AMOUNTS, custom: true }
		]
	},
	drawdown: {
		label: 'Drawdown from peak',
		fields: [
			{
				key: 'pct',
				label: 'Below peak by',
				type: 'choice',
				options: plain([5, 10, 15, 20, 30, 50], ' %'),
				custom: true
			}
		]
	},
	down: {
		label: 'Bot down',
		fields: [
			{
				key: 'statuses',
				label: 'When the bot is',
				type: 'many',
				options: [
					{ value: 'down', label: 'Down' },
					{ value: 'stale', label: 'Stale' },
					{ value: 'events_off', label: 'Events off' }
				]
			},
			FOR_S
		]
	},
	stop_reason: {
		label: 'Bot stopped',
		fields: [
			{
				key: 'reasons',
				label: 'Reasons (none ticked means any stop)',
				type: 'many',
				options: STOP_REASONS.map((r) => ({ value: r, label: r }))
			},
			{
				key: 'pattern',
				label: 'Or reason matches',
				type: 'text',
				hint: 'Optional regular expression'
			}
		]
	},
	bps_below: {
		label: 'Slow betting',
		fields: [
			{
				key: 'bps',
				label: 'Bets/s below',
				type: 'choice',
				options: plain([1, 2, 5, 10, 20, 50]),
				custom: true
			},
			FOR_S
		]
	},
	publisher_dropped: { label: 'Live events dropped', fields: [] },
	ingest_lag: {
		label: 'History copy behind',
		fields: [
			{
				key: 'seconds',
				label: 'Behind by',
				type: 'choice',
				options: seconds([300, 900, 1800, 3600, 21600]),
				custom: true,
				optional: 'When the check flags it'
			}
		]
	},
	reconcile_mismatch: { label: 'History copy differs', fields: [] }
};

/** What the editor holds: every value as the form shows it. */
export interface RuleForm {
	name: string;
	kind: Kind;
	lane: string;
	cooldown: string;
	enabled: boolean;
	values: Record<ValueKey, string>;
	custom: Record<ValueKey, string>;
	many: Record<ManyKey, string[]>;
}

const DEFAULT_VALUES: Record<ValueKey, string> = {
	period: 'session',
	mode: 'step',
	amount: '0.001',
	at: '0.001',
	pct: '10',
	for_s: '60',
	bps: '5',
	seconds: '',
	pattern: ''
};

export function emptyForm(kind: Kind = 'down'): RuleForm {
	return {
		name: KINDS[kind].label,
		kind,
		lane: 'all',
		cooldown: '600',
		enabled: true,
		values: { ...DEFAULT_VALUES },
		custom: { ...DEFAULT_VALUES, period: '', mode: '', pattern: '' },
		many: { statuses: ['down'], reasons: [] }
	};
}

function choose(form: RuleForm, key: ValueKey, raw: number | null | undefined): void {
	if (raw === null || raw === undefined) return;
	const spec = Object.values(KINDS)
		.flatMap((k) => k.fields)
		.find((f) => f.key === key);
	const known = spec?.type === 'choice' && spec.options.some((o) => o.value === String(raw));
	if (known) form.values[key] = String(raw);
	else {
		form.values[key] = CUSTOM;
		form.custom[key] = String(raw);
	}
}

/** The editor's form for an existing rule. */
export function formFor(rule: Rule): RuleForm {
	const form = emptyForm(rule.kind as Kind);
	const p = rule.params as Record<string, unknown>;
	form.name = rule.name;
	form.lane = rule.lane === null ? 'all' : String(rule.lane);
	form.cooldown = String(rule.cooldown_s);
	form.enabled = rule.enabled;
	if (typeof p.period === 'string') form.values.period = p.period;
	if (rule.kind === 'profit') {
		form.values.mode = typeof p.step === 'number' ? 'step' : 'at';
		choose(form, 'amount', (p.step ?? p.at) as number | undefined);
	}
	if (rule.kind === 'loss') choose(form, 'at', p.at as number | undefined);
	for (const key of ['pct', 'for_s', 'bps'] as const)
		choose(form, key, p[key] as number | undefined);
	if (rule.kind === 'ingest_lag') {
		if (typeof p.seconds === 'number') choose(form, 'seconds', p.seconds);
		else form.values.seconds = '';
	}
	if (Array.isArray(p.statuses)) form.many.statuses = p.statuses.map(String);
	if (Array.isArray(p.reasons)) form.many.reasons = p.reasons.map(String);
	if (typeof p.pattern === 'string') form.values.pattern = p.pattern;
	return form;
}

function number(form: RuleForm, key: ValueKey): number | null {
	const raw = form.values[key] === CUSTOM ? form.custom[key] : form.values[key];
	if (raw.trim() === '') return null;
	const value = Number(raw);
	return Number.isFinite(value) && value > 0 ? value : NaN;
}

/** The request body for the form, or the first thing to fix. */
export function bodyFor(form: RuleForm): { body: RuleBody } | { error: string } {
	const params: Record<string, unknown> = {};
	const need = (key: ValueKey, label: string): number | string => {
		const value = number(form, key);
		return value === null || Number.isNaN(value) ? `${label} must be a number above 0.` : value;
	};
	for (const field of KINDS[form.kind].fields) {
		if (field.key === 'mode') continue;
		if (field.key === 'period') params.period = form.values.period;
		else if (field.type === 'many') params[field.key] = [...form.many[field.key]];
		else if (field.type === 'text') {
			const text = form.values[field.key].trim();
			if (text) params[field.key] = text;
		} else if (field.type === 'choice' && field.optional && !form.values[field.key]) {
			continue;
		} else {
			const value = need(field.key, field.label);
			if (typeof value === 'string') return { error: value };
			const key = field.key === 'amount' ? (form.values.mode === 'at' ? 'at' : 'step') : field.key;
			params[key] = field.key === 'for_s' || field.key === 'seconds' ? Math.round(value) : value;
		}
	}
	if (form.kind === 'down' && !form.many.statuses.length) {
		return { error: 'Pick at least one bot status.' };
	}
	const name = form.name.trim();
	if (!name) return { error: 'Give the rule a name.' };
	return {
		body: {
			name,
			kind: form.kind,
			lane: form.lane === 'all' ? null : Number(form.lane),
			params,
			cooldown_s: Number(form.cooldown),
			enabled: form.enabled
		}
	};
}

/** One line saying what a rule watches, for the rules list. */
export function describe(rule: Pick<Rule, 'kind' | 'params'>): string {
	const p = rule.params as Record<string, unknown>;
	const per = typeof p.period === 'string' ? ` (${p.period})` : '';
	switch (rule.kind) {
		case 'profit':
			return typeof p.step === 'number'
				? `Profit every +${p.step}${per}`
				: `Profit reaches +${p.at}${per}`;
		case 'loss':
			return `Loss reaches -${p.at}${per}`;
		case 'drawdown':
			return `Balance ${p.pct} % below its peak`;
		case 'down':
			return `Bot ${(p.statuses as string[]).join(' or ')} for ${formatDuration(Number(p.for_s))}`;
		case 'stop_reason': {
			const reasons = (p.reasons as string[]) ?? [];
			if (p.pattern) return `Bot stopped, reason matches ${p.pattern}`;
			return reasons.length ? `Bot stopped: ${reasons.join(', ')}` : 'Bot stopped for any reason';
		}
		case 'bps_below':
			return `Below ${p.bps} bets/s for ${formatDuration(Number(p.for_s))}`;
		case 'ingest_lag':
			return typeof p.seconds === 'number'
				? `History copy ${formatDuration(p.seconds)} behind`
				: 'History copy flagged as behind';
		default:
			return KINDS[rule.kind as Kind]?.label ?? rule.kind;
	}
}

export function cooldownLabel(seconds: number): string {
	return seconds === 0 ? 'None' : formatDuration(seconds);
}

/** The changes a save would make, as lines for the confirm dialog. */
export function changes(before: Rule | null, after: RuleBody): string[] {
	const lane = (value: number | null | undefined) =>
		value == null ? 'All lanes' : `Lane ${value}`;
	const next = {
		Name: after.name,
		Watches: describe({ kind: after.kind, params: after.params ?? {} }),
		Lane: lane(after.lane),
		Cooldown: cooldownLabel(after.cooldown_s ?? 0),
		State: after.enabled ? 'On' : 'Off'
	};
	if (before === null) return Object.entries(next).map(([k, v]) => `${k}: ${v}`);
	const prev = {
		Name: before.name,
		Watches: describe(before),
		Lane: lane(before.lane),
		Cooldown: cooldownLabel(before.cooldown_s),
		State: before.enabled ? 'On' : 'Off'
	};
	const lines = (Object.keys(next) as (keyof typeof next)[])
		.filter((k) => prev[k] !== next[k])
		.map((k) => `${k}: ${prev[k]} → ${next[k]}`);
	return lines.length ? lines : ['No changes'];
}

/** How a test-fire's channels read in a toast. */
export function sentTo(channels: string[]): string {
	const names = channels.map((c) => (c === 'email' ? 'email' : 'Telegram'));
	return `Test sent to ${names.join(' and ')}.`;
}
