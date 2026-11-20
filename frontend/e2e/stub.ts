import type { Page, Route } from '@playwright/test';

export type Reply = { status: number; json?: unknown };
export type Handler = (body: unknown, url: URL) => Reply;

export const viewerMe = {
	email: 'viewer@example.test',
	csrf_token: 'csrf-viewer',
	totp_enabled: false,
	roles: [{ service: null, role: 'viewer' }]
};

export const adminMe = {
	email: 'admin@example.test',
	csrf_token: 'csrf-admin',
	totp_enabled: true,
	roles: [{ service: null, role: 'admin' }]
};

/**
 * Answer `/api/*` like the real api, keyed by "METHOD /path" without the `/api` prefix.
 * Anything not listed gets a 404, so an unexpected call fails the test visibly.
 */
export async function stubApi(page: Page, handlers: Record<string, Handler>): Promise<string[]> {
	const calls: string[] = [];
	await page.route('**/api/**', async (route: Route) => {
		const request = route.request();
		const url = new URL(request.url());
		const key = `${request.method()} ${url.pathname.replace(/^\/api/, '')}`;
		calls.push(key);
		const handler = handlers[key];
		const body = request.postData() ? JSON.parse(request.postData() as string) : null;
		const reply = handler ? handler(body, url) : { status: 404, json: { detail: 'Not found' } };
		await route.fulfill({
			status: reply.status,
			contentType: 'application/json',
			body: reply.json === undefined ? '' : JSON.stringify(reply.json)
		});
	});
	return calls;
}

export const operatorMe = {
	email: 'operator@example.test',
	csrf_token: 'csrf-operator',
	totp_enabled: false,
	roles: [{ service: null, role: 'operator' }]
};

const action = (
	name: string,
	perm: string,
	label: string,
	title: string,
	confirm: string,
	options: { job?: boolean; read?: boolean } = {}
) => ({
	name,
	label,
	title,
	effect: `Effect of ${label.toLowerCase()} on {target}.`,
	confirm,
	perm,
	typed_confirm: perm === 'control.destructive',
	confirm_first: !options.read,
	job: options.job ?? false
});

/** The control menu as `GET /{service}/control` serves it, with nothing running. */
export const controlMenu = {
	running_job: null,
	actions: [
		action('start', 'control.basic', 'Start', 'Start {service}?', 'Start'),
		action('stop', 'control.basic', 'Stop', 'Stop {service}?', 'Stop'),
		action('pause', 'control.basic', 'Pause', 'Pause {target}?', 'Pause {target}'),
		action('resume', 'control.basic', 'Resume', 'Resume {target}?', 'Resume {target}'),
		action('reload', 'control.basic', 'Reload', 'Reload {target}?', 'Reload {target}'),
		action('restart', 'control.basic', 'Restart', 'Restart {service}?', 'Restart', { job: true }),
		action('config_set', 'config.write', 'Save config', 'Save config?', 'Save config'),
		action('lanes', 'config.write', 'Apply', 'Change lanes to {count}?', 'Change lanes', {
			job: true
		}),
		action(
			'reset',
			'control.destructive',
			'Reset statistics',
			'Reset statistics for {service}?',
			'Reset statistics'
		),
		action(
			'run_reset',
			'control.destructive',
			'Run reset',
			'New run for {service}?',
			'Start new run'
		),
		action('stop_all', 'control.destructive', 'Stop all', 'Stop every {service} bot?', 'Stop all'),
		action(
			'cleanup',
			'control.destructive',
			'Clean up',
			'Delete bets older than {days} days?',
			'Delete old bets'
		),
		action('update', 'control.destructive', 'Update', 'Update {service}?', 'Update', { job: true }),
		action('logs', 'logs', 'Load', 'Load logs?', 'Load', { read: true }),
		action('logs_follow', 'logs', 'Follow', 'Follow logs?', 'Follow', { read: true, job: true }),
		action('diagnose', 'control.basic', 'Diagnose', 'Diagnose?', 'Diagnose', { read: true }),
		action('doctor', 'control.basic', 'Doctor', 'Doctor?', 'Doctor', { read: true, job: true }),
		action('benchmark', 'control.basic', 'Benchmark', 'Benchmark?', 'Benchmark', {
			read: true,
			job: true
		}),
		action('backtest', 'export', 'Run backtest', 'Backtest?', 'Run backtest', {
			read: true,
			job: true
		}),
		action('export', 'export', 'Export', 'Export?', 'Export', { read: true, job: true }),
		action('doctor_fix', 'control.destructive', 'Doctor fix', 'Run doctor fix?', 'Run doctor fix', {
			job: true
		})
	]
};
