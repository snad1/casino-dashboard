import type { Page, Route } from '@playwright/test';

export type Reply = { status: number; json?: unknown };
export type Handler = (body: unknown, url: URL) => Reply;

export const viewerMe = {
	email: 'viewer@example.test',
	tg_uid: null,
	csrf_token: 'csrf-viewer',
	totp_enabled: false,
	roles: [{ service: null, role: 'viewer' }]
};

export const adminMe = {
	email: 'admin@example.test',
	tg_uid: null,
	csrf_token: 'csrf-admin',
	totp_enabled: true,
	roles: [{ service: null, role: 'admin' }]
};

/** A Telegram account signed in through the bot's link: no email, no role on any service. */
export const tgMe = (uid: number) => ({
	email: null,
	tg_uid: uid,
	csrf_token: `csrf-tg-${uid}`,
	totp_enabled: false,
	roles: [{ service: null, role: 'tg_user' }]
});

export const WOLFBET = {
	name: 'wolfbet',
	label: 'WolfBet',
	ctl: 'wolfctl',
	installed: true,
	live: true
};

/**
 * Answer `/api/*` like the real api, keyed by "METHOD /path" without the `/api` prefix.
 * Anything not listed gets a 404, so an unexpected call fails the test visibly; the service
 * list is WolfBet alone unless the test gives its own.
 */
export async function stubApi(page: Page, given: Record<string, Handler>): Promise<string[]> {
	const handlers: Record<string, Handler> = {
		'GET /services': () => ({ status: 200, json: [WOLFBET] }),
		...given
	};
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
	tg_uid: null,
	csrf_token: 'csrf-operator',
	totp_enabled: false,
	roles: [{ service: null, role: 'operator' }]
};

/** The body fields of WolfBet's actions that the pages read (each true when required). */
const FIELDS: Record<string, Record<string, boolean>> = {
	update: { branch: false },
	export: { session_id: false, format: false },
	benchmark: { count: false, lanes: false },
	backtest: {
		simulate: false,
		replay_session: false,
		seed: false,
		sessions: false,
		runs: false,
		balance: false,
		preset: false
	}
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
	job: options.job ?? false,
	fields: FIELDS[name] ?? {}
});

/** The control menu as `GET /{service}/control` serves it, with nothing running. */
export const controlMenu = {
	running_job: null,
	actions: [
		action('start', 'control.basic', 'Start', 'Start {service}?', 'Start'),
		action('stop', 'control.basic', 'Stop all', 'Stop every lane of {service}?', 'Stop'),
		action('lane_stop', 'control.basic', 'Stop', 'Stop lane {lane}?', 'Stop lane {lane}'),
		action('lane_start', 'control.basic', 'Start', 'Start lane {lane}?', 'Start lane {lane}'),
		action(
			'lane_restart',
			'control.basic',
			'Restart',
			'Restart lane {lane}?',
			'Restart lane {lane}'
		),
		action(
			'lane_restart_end',
			'control.basic',
			'Restart at session end',
			'Restart lane {lane} at its session end?',
			'Restart at session end'
		),
		action('lane_add', 'config.write', 'Add lane', 'Add the lane {name}?', 'Add lane'),
		action('lane_remove', 'config.write', 'Remove', 'Remove lane {lane}?', 'Remove lane {lane}'),
		action('lane_rename', 'config.write', 'Rename', 'Rename lane {lane} to {name}?', 'Rename'),
		action('pause', 'control.basic', 'Pause', 'Pause {target}?', 'Pause {target}'),
		action('resume', 'control.basic', 'Resume', 'Resume {target}?', 'Resume {target}'),
		action('reload', 'control.basic', 'Reload', 'Reload {target}?', 'Reload {target}'),
		action('restart', 'control.basic', 'Restart', 'Restart {service}?', 'Restart', { job: true }),
		action(
			'restart_rolling',
			'control.basic',
			'Restart all at session end',
			'Restart every lane at its session end?',
			'Restart at session end'
		),
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
		action('archives', 'control.basic', 'List archives', 'List archives?', 'List archives', {
			read: true
		}),
		action('doctor_fix', 'control.destructive', 'Doctor fix', 'Run doctor fix?', 'Run doctor fix', {
			job: true
		}),
		action('start_all', 'control.basic', 'Start all', 'Start every {service} bot?', 'Start all'),
		action('status_all', 'control.basic', 'Check', 'Check services?', 'Check', { read: true }),
		action('version', 'control.basic', 'Version', 'Show version?', 'Version', { read: true }),
		...[['tg', 'the Telegram bot']].flatMap(([group, unit]) => [
			action(`${group}_start`, 'control.basic', 'Start', `Start ${unit}?`, 'Start'),
			action(`${group}_stop`, 'control.basic', 'Stop', `Stop ${unit}?`, 'Stop'),
			action(`${group}_restart`, 'control.basic', 'Restart', `Restart ${unit}?`, 'Restart'),
			action(`${group}_status`, 'control.basic', 'Status', `Status of ${unit}?`, 'Status', {
				read: true
			})
		])
	]
};

/** goated's menu: WolfBet's without the live signals, with goated's own bodies. */
export const goatedControlMenu = {
	running_job: null,
	actions: controlMenu.actions
		.filter((a) => !['pause', 'resume', 'reload'].includes(a.name))
		.map((a) => {
			if (a.name === 'update') return { ...a, fields: {} };
			if (a.name === 'export') return { ...a, fields: { session_id: true, format: false } };
			if (a.name === 'benchmark') {
				return {
					...a,
					title: 'Benchmark the {service} api?',
					effect: 'Times {count} calls. Each is a 0-amount bet, so no balance is risked.',
					confirm_first: true,
					fields: { count: false }
				};
			}
			if (a.name === 'backtest') {
				return {
					...a,
					fields: { simulate: false, replay_session: false, seed: false, preset: false }
				};
			}
			return a;
		})
};
