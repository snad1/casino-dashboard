import { execFileSync } from 'node:child_process';
import { createHmac, randomBytes } from 'node:crypto';
import { resolve } from 'node:path';
import { expect, type Page } from '@playwright/test';

const ROOT = resolve(import.meta.dirname, '../..');
export const EMAIL = process.env.E2E_EMAIL ?? 'viewer@e2e.test';
/** Seeded by `E2E_VIEWERS=1`; a second account so another viewer's view can be checked. */
export const OTHER_EMAIL = 'viewer1@e2e.test';

/** Signed-in browser state saved once per run by `auth.setup.ts`, so the login limit is never hit. */
export const STATE = {
	viewer: 'test-results/.auth/viewer.json',
	other: 'test-results/.auth/viewer1.json'
};

/** Telegram users `tools/e2e_seed.py` seeds, with how many sessions each has. */
export const TG_USERS = { first: 7001, second: 7002 } as const;
export const TG_SESSIONS = { [TG_USERS.first]: 2, [TG_USERS.second]: 3 } as const;

function secret(name: string): string {
	return execFileSync('cat', [resolve(ROOT, `deploy/e2e-secrets/${name}.txt`)], {
		encoding: 'utf8'
	}).trim();
}

/**
 * A `/web` link as the bot signs it (`contracts/fixtures/tg_login_v1/`): compact JSON with
 * sorted keys, HMAC-SHA256 with the stack's `tg_login_secret`, valid for five minutes.
 */
export function signedLink(uid: number): string {
	const payload = JSON.stringify({
		exp: Math.floor(Date.now() / 1000) + 300,
		id: randomBytes(16).toString('base64url'),
		uid,
		v: 1
	});
	const body = Buffer.from(payload).toString('base64url');
	const mac = createHmac('sha256', secret('tg_login_secret')).update(body).digest('base64url');
	return `/tg/login#token=${body}.${mac}`;
}

function password(): string {
	if (process.env.E2E_PASSWORD) return process.env.E2E_PASSWORD;
	return execFileSync('cat', [resolve(ROOT, 'deploy/e2e-secrets/e2e_password.txt')], {
		encoding: 'utf8'
	}).trim();
}

/** Sign in through the login form and land on the WolfBet dashboard with live data. */
export async function signIn(page: Page, email = EMAIL): Promise<void> {
	await page.goto('/login');
	await page.getByLabel('Email').fill(email);
	await page.getByLabel('Password').fill(password());
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/wolfbet$/);
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
}

/** Open the WolfBet dashboard with an already signed-in state and wait for live data. */
export async function openDashboard(page: Page): Promise<void> {
	await page.goto('/wolfbet');
	await expect(page).toHaveURL(/\/wolfbet$/);
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
}

/** Run docker compose against the e2e project, the same way `tools/e2e_stack.sh` does. */
export function compose(...args: string[]): void {
	execFileSync(resolve(ROOT, 'tools/e2e_stack.sh'), ['compose', ...args], { stdio: 'inherit' });
}
