import { expect, test, type Locator } from '@playwright/test';
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

test('a role can be limited to several lanes, then changed', async ({ page }) => {
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
		'GET /wolfbet/config': () => ({ status: 200, json: { lane_ids: [1, 3, 8, 9] } }),
		'PUT /admin/users/2/role': (body) => {
			sent.push(body);
			const { lanes } = body as { lanes: number[] };
			friend.roles = lanes.map((lane) => ({ service: 'wolfbet', role: 'viewer', lane }));
			return { status: 204 };
		}
	});
	// The phone tab bar covers the row's bottom edge, where a pointer click scrolls it to.
	const toggle = (box: Locator) => box.press('Space');
	await page.goto('/admin/users');
	const row = page.getByRole('row', { name: /friend@example\.test/ });
	await row.locator('summary').press('Enter');
	const lanes = row.getByRole('group', { name: 'Lanes for friend@example.test' });
	await toggle(lanes.getByLabel('Lane 8'));
	await toggle(lanes.getByLabel('Lane 9'));
	await row.getByRole('button', { name: 'Save lanes' }).press('Enter');
	await expect(
		page.getByRole('dialog', { name: 'Make friend@example.test viewer on lanes 8, 9?' })
	).toBeVisible();
	await page.getByRole('button', { name: 'Make viewer' }).click();
	await expect(page.getByRole('status')).toContainText(
		'friend@example.test is now viewer on wolfbet lanes 8, 9.'
	);
	await expect(page.getByText('viewer on wolfbet lanes 8, 9', { exact: true })).toBeVisible();

	await expect(row.getByText('On lanes 8, 9')).toBeVisible();
	if (!(await lanes.isVisible())) await row.locator('summary').press('Enter');
	await toggle(lanes.getByLabel('Lane 8'));
	await toggle(lanes.getByLabel('Lane 3'));
	await row.getByRole('button', { name: 'Save lanes' }).press('Enter');
	await page.getByRole('button', { name: 'Make viewer' }).click();
	await expect(page.getByText('viewer on wolfbet lanes 3, 9', { exact: true })).toBeVisible();
	expect(sent).toEqual([
		{ role: 'viewer', service: 'wolfbet', lanes: [8, 9] },
		{ role: 'viewer', service: 'wolfbet', lanes: [3, 9] }
	]);
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
	expect(sent).toEqual([{ role: 'lane_manager', service: 'wolfbet', lanes: [] }]);
});

test('an invite can name several lanes, or make a lane manager', async ({ page }) => {
	const sent: unknown[] = [];
	await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: adminMe }),
		'GET /admin/users': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/config': () => ({ status: 200, json: { lane_ids: [1, 3, 8, 9] } }),
		'POST /auth/invites': (body) => {
			sent.push(body);
			return { status: 204 };
		}
	});
	await page.goto('/admin/users');
	await expect(page.getByText('Viewer: Sees the live view')).toBeVisible();
	await expect(page.getByText('What each role can do')).toBeVisible();
	for (const name of ['Viewer', 'Operator', 'Admin', 'Lane manager', 'Owner', 'On some lanes']) {
		await expect(page.locator('dt', { hasText: new RegExp(`^${name}$`) })).toBeVisible();
	}
	await page.getByLabel('Email to invite').fill('friend@example.test');
	const choice = page.getByRole('group', { name: 'Lanes on wolfbet' });
	await choice.getByLabel('Lane 9').check();
	await choice.getByLabel('Lane 8').check();
	await page.getByRole('button', { name: 'Invite' }).click();
	await expect(page.getByText('join as viewer on wolfbet lanes 8, 9')).toBeVisible();
	await page.getByRole('button', { name: 'Send invite' }).click();
	await expect(page.getByRole('status')).toContainText('Invite sent to friend@example.test.');

	await page.getByLabel('Email to invite').fill('manager@example.test');
	const invite = page.locator('form').filter({ has: page.getByLabel('Email to invite') });
	await invite.getByRole('combobox').first().selectOption('lane_manager');
	await expect(page.getByText('Lane manager: Adds lanes of their own')).toBeVisible();
	await expect(page.getByRole('group', { name: 'Lanes on wolfbet' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Invite' }).click();
	await page.getByRole('button', { name: 'Send invite' }).click();
	await expect(page.getByRole('status')).toContainText('Invite sent to manager@example.test.');
	expect(sent).toEqual([
		{ email: 'friend@example.test', role: 'viewer', service: 'wolfbet', lanes: [8, 9] },
		{ email: 'manager@example.test', role: 'lane_manager', service: 'wolfbet', lanes: [] }
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
