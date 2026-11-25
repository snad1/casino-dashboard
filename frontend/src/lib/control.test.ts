import { describe, expect, it } from 'vitest';
import {
	addLine,
	BUSY_REASON,
	disabledReason,
	failureMessage,
	fill,
	MAX_JOB_LINES,
	newKey,
	targetLabel,
	type Action
} from './control';
import type { JobLine } from './live/protocol';
import type { Me } from './permissions';

const action = (perm: string): Action => ({
	name: 'x',
	label: 'X',
	title: '',
	effect: '',
	confirm: '',
	perm,
	typed_confirm: false,
	confirm_first: true,
	job: false
});
const me = (role: string): Me => ({
	email: 'a@b.test',
	tg_uid: null,
	csrf_token: 't',
	totp_enabled: true,
	roles: [{ role, service: null }]
});
const line = (seq: number): JobLine => ({ topic: 't', seq, stream: 'stdout', line: `l${seq}` });

describe('fill', () => {
	it('fills the target, the service and body fields', () => {
		expect(fill('Pause {target}?', { target: 2 }, 'wolfbet')).toBe('Pause lane 2?');
		expect(fill('Pause {target}?', { target: 'all' }, 'wolfbet')).toBe('Pause all lanes?');
		expect(fill('Reset {service}?', {}, 'wolfbet')).toBe('Reset wolfbet?');
		expect(fill('Older than {days} days', { days: 7 }, 'w')).toBe('Older than 7 days');
		expect(fill('Bet {amount}', { amount: 1e-8 }, 'w')).toBe('Bet 0.00000001');
	});

	it('reads optional fields naturally', () => {
		expect(fill('Pull code{branch}.', {}, 'w')).toBe('Pull code.');
		expect(fill('Pull code{branch}.', { branch: 'dev' }, 'w')).toBe('Pull code from branch dev.');
		expect(fill('The {split} split', {}, 'w')).toBe('The current split');
		expect(targetLabel(undefined)).toBe('all lanes');
	});
});

describe('disabledReason', () => {
	it('names the missing role first, then a running job', () => {
		expect(disabledReason(action('control.destructive'), me('operator'), 'wolfbet', false)).toBe(
			'Needs admin role'
		);
		expect(disabledReason(action('control.basic'), me('operator'), 'wolfbet', true)).toBe(
			BUSY_REASON
		);
		expect(disabledReason(action('control.basic'), me('operator'), 'wolfbet', false)).toBeNull();
	});
});

describe('job lines', () => {
	it('keeps each line once, in order, and caps the buffer', () => {
		let lines = [line(0), line(1)];
		expect(addLine(lines, line(1))).toBe(lines);
		expect(addLine(lines, line(0))).toBe(lines);
		lines = addLine(lines, line(2));
		expect(lines.map((l) => l.seq)).toEqual([0, 1, 2]);
		let many: JobLine[] = [];
		for (let seq = 0; seq <= MAX_JOB_LINES; seq++) many = addLine(many, line(seq));
		expect(many).toHaveLength(MAX_JOB_LINES);
		expect(many.at(0)?.seq).toBe(1);
	});
});

describe('newKey', () => {
	it('matches the api key format and differs each time', () => {
		const key = newKey();
		expect(key).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
		expect(newKey()).not.toBe(key);
	});
});

describe('failureMessage', () => {
	const out = (output: string | null, exit_code: number | null = 2) =>
		({ status: 'failed', output, exit_code }) as Parameters<typeof failureMessage>[1];

	it("shows the bot's own first line", () => {
		expect(failureMessage('Remove lane 2', out('\n\x1b[31mNo lane 2.\x1b[0m\nmore\n'))).toBe(
			'Remove lane 2 failed: No lane 2.'
		);
	});

	it('falls back to the exit code when nothing was printed', () => {
		expect(failureMessage('Remove lane 2', out(null))).toBe('Remove lane 2 failed (exit code 2).');
		expect(failureMessage('Stop', out('  \n', null))).toBe('Stop failed (exit code unknown).');
	});

	it('caps a long line', () => {
		expect(failureMessage('X', out('a'.repeat(500))).length).toBe('X failed: '.length + 200);
	});
});
