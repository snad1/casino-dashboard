import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { adminMe, stubApi, type Handler } from './stub';

const SESSION = '5e'.repeat(16);
const ownerMe = {
	email: 'owner@example.test',
	csrf_token: 'csrf-owner',
	totp_enabled: true,
	roles: [{ service: null, role: 'owner' }]
};
const CAST = [
	'{"version": 2, "width": 80, "height": 24, "timestamp": 1}',
	'[0.05, "i", "secret-typed"]',
	'[0.1, "o", "replayed-output\\r\\n"]'
].join('\n');

const control = (body: object) =>
	Buffer.concat([Buffer.from([1]), Buffer.from(JSON.stringify(body))]);
const output = (text: string) => Buffer.concat([Buffer.from([0]), Buffer.from(text)]);

interface Rig {
	frames: Buffer[];
	socket: () => WebSocketRoute;
}

async function rig(page: Page, me: object, handlers: Record<string, Handler> = {}): Promise<Rig> {
	let stepped = false;
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'live', expires_in: 30 } }),
		'POST /terminal/ticket': () =>
			stepped
				? { status: 200, json: { ticket: 'term-ticket', expires_in: 30 } }
				: {
						status: 403,
						json: {
							detail: 'Confirm with your authenticator code to continue.',
							step_up_required: true
						}
					},
		'POST /auth/step-up': () => {
			stepped = true;
			return { status: 204 };
		},
		...handlers
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => undefined);
	const frames: Buffer[] = [];
	let socket: WebSocketRoute | null = null;
	await page.routeWebSocket(/\/ws\/terminal\?ticket=term-ticket&cols=\d+&rows=\d+$/, (ws) => {
		socket = ws;
		ws.onMessage((raw) => {
			if (typeof raw !== 'string') frames.push(raw);
		});
		ws.send(control({ session_id: SESSION, idle_s: 600, max_s: 3600 }));
	});
	return {
		frames,
		socket: () => {
			if (!socket) throw new Error('terminal socket was never opened');
			return socket;
		}
	};
}

const typed = (frames: Buffer[]) =>
	frames
		.filter((f) => f[0] === 0)
		.map((f) => f.subarray(1).toString())
		.join('');

test('the owner opens the terminal with a code, types, sees output and a close reason', async ({
	page,
	isMobile
}) => {
	const { frames, socket } = await rig(page, ownerMe);
	await page.goto('/wolfbet/terminal');
	await expect(page.getByText('is recorded, and the owner')).toBeVisible();
	await page.getByRole('button', { name: 'Open terminal' }).click();
	await expect(page.getByRole('heading', { name: "Confirm it's you" })).toBeVisible();
	const prompt = page.getByRole('dialog', { name: "Confirm it's you" });
	await prompt.getByLabel('Authenticator code').fill('123456');
	await prompt.getByRole('button', { name: 'Confirm' }).click();

	await expect(page.getByText('This session is recorded.')).toBeVisible();
	await expect(page.getByText(/Idle closes in \d+:\d\d/)).toBeVisible();
	await expect.poll(() => frames.some((f) => f[0] === 1 && f.length === 5)).toBe(true);

	await page.getByLabel('Terminal', { exact: true }).click();
	await page.keyboard.type('ls');
	await expect.poll(() => typed(frames)).toContain('ls');

	socket().send(output('hello-from-host\r\n'));
	await expect(page.getByText('hello-from-host')).toBeVisible();

	if (isMobile) {
		await page.getByRole('button', { name: 'Esc' }).click();
		await expect.poll(() => typed(frames)).toContain('\x1b');
		await page.getByRole('button', { name: 'Ctrl' }).click();
		await expect(page.getByRole('button', { name: 'Ctrl' })).toHaveAttribute(
			'aria-pressed',
			'true'
		);
	} else {
		await expect(page.getByRole('group', { name: 'Terminal keys' })).toHaveCount(0);
	}

	socket().send(control({ reason: 'idle' }));
	await socket().close({ code: 1000 });
	await expect(page.getByText('The terminal closed after 10 minutes without input.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Open again' })).toBeVisible();
});

test('a terminal already open elsewhere is explained', async ({ page }) => {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: ownerMe }),
		'POST /ws-ticket': () => ({ status: 200, json: { ticket: 'live', expires_in: 30 } }),
		'POST /terminal/ticket': () => ({ status: 200, json: { ticket: 'busy', expires_in: 30 } })
	});
	await page.routeWebSocket(/\/ws\?ticket=/, () => undefined);
	await page.routeWebSocket(/\/ws\/terminal\?ticket=busy/, (ws) => {
		ws.send(control({ reason: 'busy' }));
		void ws.close({ code: 1013 });
	});
	await page.goto('/wolfbet/terminal');
	await page.getByRole('button', { name: 'Open terminal' }).click();
	await expect(page.getByText('A terminal is already open. Close it first.')).toBeVisible();
});

test('an admin cannot use the terminal or replay recordings', async ({ page }) => {
	await rig(page, adminMe, {
		'GET /audit': () => ({
			status: 200,
			json: {
				items: [
					{
						id: 1,
						ts: '2026-11-20T10:00:00Z',
						actor_id: 1,
						actor_email: 'owner@example.test',
						roles: null,
						service: null,
						action: 'WS /ws/terminal',
						args: { session_id: SESSION },
						result: 'opened',
						ip: '203.0.113.9',
						request_id: null
					}
				],
				next_before_id: null
			}
		})
	});
	await page.goto('/wolfbet/terminal');
	await expect(page.getByText("You don't have access to this page.")).toBeVisible();
	await expect(page.getByRole('button', { name: 'Open terminal' })).toHaveCount(0);
	await page.goto('/admin/audit');
	await expect(page.getByText('WS /ws/terminal')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Replay' })).toHaveCount(0);
});

test('the owner replays a recording from the audit log', async ({ page }) => {
	await rig(page, ownerMe, {
		'GET /audit': () => ({
			status: 200,
			json: {
				items: [
					{
						id: 2,
						ts: '2026-11-20T10:00:00Z',
						actor_id: 1,
						actor_email: 'owner@example.test',
						roles: null,
						service: null,
						action: 'WS /ws/terminal',
						args: { session_id: SESSION },
						result: 'opened',
						ip: '203.0.113.9',
						request_id: null
					}
				],
				next_before_id: null
			}
		})
	});
	await page.route(`**/api/terminal/recordings/${SESSION}`, (route) =>
		route.fulfill({ status: 200, contentType: 'text/plain', body: CAST })
	);
	await page.goto('/admin/audit');
	await page.getByRole('link', { name: 'Replay' }).click();
	await expect(page.getByRole('heading', { name: 'Terminal replay' })).toBeVisible();
	await expect(page.getByText('replayed-output')).toBeVisible();
	await expect(page.getByText('secret-typed')).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Play' })).toBeVisible();
});

test('the terminal page has no accessibility violations', async ({ page }) => {
	await rig(page, ownerMe);
	await page.goto('/wolfbet/terminal');
	await expect(page.getByRole('button', { name: 'Open terminal' })).toBeVisible();
	const results = await new AxeBuilder({ page }).analyze();
	expect(results.violations).toEqual([]);
});
