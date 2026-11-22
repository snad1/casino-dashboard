import { describe as group, expect, it } from 'vitest';
import {
	CUSTOM,
	bodyFor,
	changes,
	describe,
	emptyForm,
	formFor,
	KINDS,
	sentTo,
	type Rule
} from './alerts';

const rule = (over: Partial<Rule> = {}): Rule => ({
	id: 1,
	name: 'Bot down',
	kind: 'down',
	lane: null,
	params: { statuses: ['down'], for_s: 60 },
	cooldown_s: 600,
	enabled: true,
	...over
});

group('rule form', () => {
	it('turns dropdown choices into params', () => {
		const form = emptyForm('down');
		form.values.for_s = '300';
		form.many.statuses = ['down', 'stale'];
		form.lane = '2';
		expect(bodyFor(form)).toEqual({
			body: {
				name: 'Bot down',
				kind: 'down',
				lane: 2,
				params: { statuses: ['down', 'stale'], for_s: 300 },
				cooldown_s: 600,
				enabled: true
			}
		});
	});

	it('uses a custom amount and picks at or step for profit', () => {
		const form = emptyForm('profit');
		form.values.amount = CUSTOM;
		form.custom.amount = '0.0025';
		form.values.mode = 'at';
		expect(bodyFor(form)).toMatchObject({ body: { params: { period: 'session', at: 0.0025 } } });
		form.values.mode = 'step';
		expect(bodyFor(form)).toMatchObject({ body: { params: { step: 0.0025 } } });
	});

	it('says what to fix instead of sending a bad value', () => {
		const form = emptyForm('drawdown');
		form.values.pct = CUSTOM;
		form.custom.pct = 'lots';
		expect(bodyFor(form)).toEqual({ error: 'Below peak by must be a number above 0.' });
		const down = emptyForm('down');
		down.many.statuses = [];
		expect(bodyFor(down)).toEqual({ error: 'Pick at least one bot status.' });
		const nameless = emptyForm('loss');
		nameless.name = '  ';
		expect(bodyFor(nameless)).toEqual({ error: 'Give the rule a name.' });
	});

	it('leaves optional and empty fields out', () => {
		const lag = emptyForm('ingest_lag');
		expect(bodyFor(lag)).toMatchObject({ body: { params: {} } });
		const stop = emptyForm('stop_reason');
		expect(bodyFor(stop)).toMatchObject({ body: { params: { reasons: [] } } });
	});

	it('round trips a saved rule, presets and custom values alike', () => {
		const saved = rule({ params: { statuses: ['down'], for_s: 75 }, lane: 3 });
		const form = formFor(saved);
		expect(form.values.for_s).toBe(CUSTOM);
		expect(form.custom.for_s).toBe('75');
		expect(bodyFor(form)).toMatchObject({ body: { lane: 3, params: { for_s: 75 } } });
		const step = formFor(rule({ kind: 'profit', params: { period: 'run', step: 0.001 } }));
		expect([step.values.mode, step.values.amount, step.values.period]).toEqual([
			'step',
			'0.001',
			'run'
		]);
	});
});

group('rule text', () => {
	it('describes what a rule watches', () => {
		expect(describe(rule())).toBe('Bot down for 1m 0s');
		expect(describe(rule({ kind: 'profit', params: { period: 'day', step: 0.01 } }))).toBe(
			'Profit every +0.01 (day)'
		);
		expect(describe(rule({ kind: 'loss', params: { period: 'session', at: 0.00000001 } }))).toBe(
			'Loss reaches -0.00000001 (session)'
		);
		expect(describe(rule({ kind: 'stop_reason', params: { reasons: [] } }))).toBe(
			'Bot stopped for any reason'
		);
	});

	it('lists only what a save changes', () => {
		const before = rule();
		const form = formFor(before);
		form.cooldown = '3600';
		const result = bodyFor(form);
		if (!('body' in result)) throw new Error(result.error);
		expect(changes(before, result.body)).toEqual(['Cooldown: 10m 0s → 1h 0m 0s']);
		const same = bodyFor(formFor(before));
		if (!('body' in same)) throw new Error(same.error);
		expect(changes(before, same.body)).toEqual(['No changes']);
		expect(changes(null, result.body)[0]).toBe('Name: Bot down');
	});

	it('names the channels a test went to', () => {
		expect(sentTo(['email', 'telegram'])).toBe('Test sent to email and Telegram.');
		expect(sentTo(['push'])).toBe('Test sent to push.');
	});
});

group('amount presets', () => {
	it('start at the smallest unit and read as plain decimals', () => {
		const field = KINDS.profit.fields.find((f) => f.key === 'amount');
		const options = field?.type === 'choice' ? field.options : [];
		expect(options[0]).toEqual({ value: '0.00000001', label: '0.00000001' });
		expect(options.map((o) => o.label)).toContain('0.01');
	});

	it('reopen a saved smallest-unit rule on its preset', () => {
		const form = formFor(rule({ kind: 'profit', params: { period: 'run', at: 0.00000001 } }));
		expect(form.values.amount).toBe('0.00000001');
		expect(bodyFor(form)).toMatchObject({ body: { params: { at: 0.00000001 } } });
	});
});
