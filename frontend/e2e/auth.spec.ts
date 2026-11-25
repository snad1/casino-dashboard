import { expect, test } from '@playwright/test';
import { adminMe, stubApi, viewerMe } from './stub';

const CODE_NEEDED = 'Enter the code from your authenticator app.';
const TOO_MANY = 'Too many sign-in attempts. Wait a minute and try again.';
const STEP_UP_NEEDED = 'Confirm with your authenticator code to continue.';

test('sign-in with a 2FA code returns to the page that was asked for', async ({ page }) => {
	let signedIn = false;
	await stubApi(page, {
		'GET /auth/me': () =>
			signedIn ? { status: 200, json: viewerMe } : { status: 401, json: { detail: 'x' } },
		'POST /auth/login': (body) => {
			const { code } = body as { code: string | null };
			if (!code) return { status: 401, json: { detail: CODE_NEEDED, totp_required: true } };
			signedIn = true;
			return { status: 204 };
		}
	});
	await page.goto('/wolfbet');
	await expect(page).toHaveURL('/login?next=%2Fwolfbet');
	await page.getByLabel('Email').fill('viewer@example.test');
	await page.getByLabel('Password').fill('correct horse battery staple');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('status')).toContainText(CODE_NEEDED);
	await page.getByLabel('Authenticator code').fill('123456');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL('/wolfbet');
	await expect(page.getByRole('heading', { name: 'Chart' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Bet tape' })).toBeVisible();
});

test('too many attempts shows the wait message and keeps the form usable', async ({ page }) => {
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 401, json: { detail: 'x' } }),
		'POST /auth/login': () => ({ status: 429, json: { detail: TOO_MANY } })
	});
	await page.goto('/login');
	await page.getByLabel('Email').fill('someone@example.test');
	await page.getByLabel('Password').fill('wrong');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('alert')).toHaveText(`Problem: ${TOO_MANY}`, { useInnerText: true });
	await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled();
});

test('an invite link creates the account and drops the token from the address', async ({
	page
}) => {
	let sent: unknown = null;
	await stubApi(page, {
		'POST /auth/invites/accept': (body) => {
			sent = body;
			return { status: 204 };
		}
	});
	const token = 'a'.repeat(43);
	await page.goto(`/invite#token=${token}`);
	await expect(page).toHaveURL('/invite');
	await page.getByLabel('Choose a password').fill('long unrelated words here');
	await page.getByLabel('Repeat the password').fill('long unrelated words here');
	await page.getByRole('button', { name: 'Create account' }).click();
	await expect(page.getByRole('status')).toContainText('Account created. You can sign in now.');
	expect(sent).toEqual({ token, password: 'long unrelated words here' });
});

test('an admin invites a user, and a role change asks for 2FA first', async ({ page }) => {
	let stepped = false;
	const users = [
		{ ...adminMe, id: 1, disabled: false, created_at: '2026-11-01T00:00:00Z' },
		{
			id: 2,
			email: 'op@example.test',
			roles: [{ service: null, role: 'viewer' }],
			disabled: false,
			totp_enabled: false,
			created_at: '2026-11-02T00:00:00Z'
		}
	];
	const calls = await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'GET /admin/users': () => ({ status: 200, json: { items: users } }),
		'POST /auth/invites': () => ({ status: 204 }),
		'PUT /admin/users/2/role': () =>
			stepped
				? { status: 204 }
				: { status: 403, json: { detail: STEP_UP_NEEDED, step_up_required: true } },
		'POST /auth/step-up': () => {
			stepped = true;
			return { status: 204 };
		}
	});
	await page.goto('/admin/users');
	await page.getByLabel('Email to invite').fill('new@example.test');
	await page.getByRole('button', { name: 'Invite' }).click();
	await page.getByRole('button', { name: 'Send invite' }).click();
	await expect(page.getByRole('status')).toContainText('Invite sent to new@example.test.');

	await page.getByLabel('Role for op@example.test').selectOption('operator');
	await page.getByRole('button', { name: 'Make operator' }).click();
	await expect(page.getByRole('heading', { name: "Confirm it's you" })).toBeVisible();
	await page.getByLabel('Authenticator code').fill('123456');
	await page.getByRole('button', { name: 'Confirm' }).click();
	await expect(page.getByRole('status')).toContainText(
		'op@example.test is now operator on every service.'
	);
	expect(calls.filter((c) => c === 'PUT /admin/users/2/role')).toHaveLength(2);
});

test('a role can be limited to one lane', async ({ page }) => {
	const sent: unknown[] = [];
	const friend: { roles: { service: string | null; role: string; lane: number | null }[] } & Record<
		string,
		unknown
	> = {
		id: 2,
		email: 'friend@example.test',
		roles: [{ service: null, role: 'viewer', lane: null }],
		disabled: false,
		totp_enabled: false,
		created_at: '2026-11-02T00:00:00Z'
	};
	const users = [
		{ ...adminMe, id: 1, disabled: false, created_at: '2026-11-01T00:00:00Z' },
		friend
	];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'GET /admin/users': () => ({ status: 200, json: { items: users } }),
		'PUT /admin/users/2/role': (body) => {
			sent.push(body);
			friend.roles = [{ service: 'wolfbet', role: 'viewer', lane: 3 }];
			return { status: 204 };
		}
	});
	await page.goto('/admin/users');
	await page.getByLabel('Lane for friend@example.test').fill('3');
	await page.getByLabel('Lane for friend@example.test').press('Tab');
	await expect(
		page.getByRole('dialog', { name: 'Make friend@example.test viewer on lane 3?' })
	).toBeVisible();
	await page.getByRole('button', { name: 'Make viewer' }).click();
	await expect(page.getByRole('status')).toContainText(
		'friend@example.test is now viewer on wolfbet lane 3.'
	);
	expect(sent).toEqual([{ role: 'viewer', service: 'wolfbet', lane: 3 }]);
	await expect(page.getByText('viewer on wolfbet lane 3', { exact: true })).toBeVisible();
});

test('a lane manager is granted on this service', async ({ page }) => {
	const sent: unknown[] = [];
	const users = [
		{ ...adminMe, id: 1, disabled: false, created_at: '2026-11-01T00:00:00Z' },
		{
			id: 2,
			email: 'friend@example.test',
			roles: [{ service: null, role: 'viewer', lane: null }],
			disabled: false,
			totp_enabled: false,
			created_at: '2026-11-02T00:00:00Z'
		}
	];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'GET /admin/users': () => ({ status: 200, json: { items: users } }),
		'PUT /admin/users/2/role': (body) => {
			sent.push(body);
			return { status: 204 };
		}
	});
	await page.goto('/admin/users');
	await page.getByLabel('Role for friend@example.test').selectOption('lane_manager');
	await page.getByRole('button', { name: 'Make lane_manager' }).click();
	await expect(page.getByRole('status')).toContainText(
		'friend@example.test is now lane_manager on wolfbet.'
	);
	expect(sent).toEqual([{ role: 'lane_manager', service: 'wolfbet', lane: null }]);
});

test('an invite can name one lane, or make a lane manager', async ({ page }) => {
	const sent: unknown[] = [];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'GET /admin/users': () => ({ status: 200, json: { items: [] } }),
		'POST /auth/invites': (body) => {
			sent.push(body);
			return { status: 204 };
		}
	});
	await page.goto('/admin/users');
	await expect(page.getByText('Viewer: Sees the live view')).toBeVisible();
	await expect(page.getByText('What each role can do')).toBeVisible();
	for (const name of ['Viewer', 'Operator', 'Admin', 'Lane manager', 'Owner', 'On one lane']) {
		await expect(page.locator('dt', { hasText: new RegExp(`^${name}$`) })).toBeVisible();
	}
	await page.getByLabel('Email to invite').fill('friend@example.test');
	await page.getByLabel('Lane', { exact: true }).fill('3');
	await page.getByRole('button', { name: 'Invite' }).click();
	await expect(page.getByText('join as viewer on wolfbet lane 3')).toBeVisible();
	await page.getByRole('button', { name: 'Send invite' }).click();
	await expect(page.getByRole('status')).toContainText('Invite sent to friend@example.test.');

	await page.getByLabel('Email to invite').fill('manager@example.test');
	const invite = page.locator('form').filter({ has: page.getByLabel('Email to invite') });
	await invite.getByRole('combobox').first().selectOption('lane_manager');
	await expect(page.getByText('Lane manager: Adds lanes of their own')).toBeVisible();
	await page.getByRole('button', { name: 'Invite' }).click();
	await page.getByRole('button', { name: 'Send invite' }).click();
	await expect(page.getByRole('status')).toContainText('Invite sent to manager@example.test.');
	expect(sent).toEqual([
		{ email: 'friend@example.test', role: 'viewer', service: 'wolfbet', lane: 3 },
		{ email: 'manager@example.test', role: 'lane_manager', service: 'wolfbet', lane: null }
	]);
});

test('only the owner changes a lane manager quota', async ({ page }) => {
	const sent: unknown[] = [];
	const manager = {
		id: 2,
		email: 'manager@example.test',
		roles: [{ service: 'wolfbet', role: 'lane_manager', lane: null }],
		disabled: false,
		totp_enabled: true,
		lane_quota: 1,
		created_at: '2026-11-02T00:00:00Z'
	};
	const ownerMe = {
		...adminMe,
		email: 'owner@example.test',
		roles: [{ service: null, role: 'owner' }]
	};
	let me = adminMe;
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: me }),
		'GET /admin/users': () => ({ status: 200, json: { items: [manager] } }),
		'PUT /admin/users/2/lane-quota': (body) => {
			sent.push(body);
			return { status: 204 };
		}
	});
	await page.goto('/admin/users');
	await expect(page.getByText('May run up to 1 lane')).toBeVisible();
	await expect(page.getByLabel('Lane quota for manager@example.test')).toHaveCount(0);

	me = ownerMe;
	await page.reload();
	await page.getByLabel('Lane quota for manager@example.test').fill('3');
	await page.getByLabel('Lane quota for manager@example.test').press('Tab');
	await page.getByRole('button', { name: 'Set quota' }).click();
	await expect(page.getByRole('status')).toContainText('manager@example.test may now run 3 lanes.');
	expect(sent).toEqual([{ lane_quota: 3 }]);
});

test('2FA setup shows a QR code to scan alongside the key', async ({ page }) => {
	const secret = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /auth/totp/enrol': () => ({
			status: 200,
			json: {
				secret,
				otpauth_uri: `otpauth://totp/Casino%20dashboard:viewer%40example.test?secret=${secret}&issuer=Casino%20dashboard`
			}
		})
	});
	await page.goto('/totp');
	await page.getByRole('button', { name: 'Set up two-factor sign-in' }).click();
	await expect(page.getByRole('img', { name: 'QR code for authenticator setup' })).toBeVisible();
	await expect(page.getByText(secret)).toBeVisible();
	await expect(page.getByRole('link', { name: 'Open in authenticator' })).toBeVisible();
});

test('a viewer is told the users page is not for them', async ({ page }) => {
	await stubApi(page, { 'GET /auth/me': () => ({ status: 200, json: viewerMe }) });
	await page.goto('/admin/users');
	await expect(
		page.getByRole('heading', { name: "You don't have access to this page." })
	).toBeVisible();
	await expect(page.getByRole('link', { name: 'Users' })).toHaveCount(0);
});

test('on a phone the sections are a bottom tab bar', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await stubApi(page, { 'GET /auth/me': () => ({ status: 200, json: viewerMe }) });
	await page.goto('/wolfbet');
	const tabs = page.getByRole('navigation', { name: 'Tabs' });
	await expect(tabs).toBeVisible();
	await expect(tabs.getByRole('link')).toHaveText(['Live', 'Sessions', 'Control', 'Alerts']);
	await expect(page.getByRole('navigation', { name: 'Main' })).toBeHidden();
});
