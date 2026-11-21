import { execFileSync } from 'node:child_process';
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
