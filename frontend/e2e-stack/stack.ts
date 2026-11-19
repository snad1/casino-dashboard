import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, type Page } from '@playwright/test';

const ROOT = resolve(import.meta.dirname, '../..');
export const EMAIL = process.env.E2E_EMAIL ?? 'viewer@e2e.test';

function password(): string {
	if (process.env.E2E_PASSWORD) return process.env.E2E_PASSWORD;
	return execFileSync('cat', [resolve(ROOT, 'deploy/e2e-secrets/e2e_password.txt')], {
		encoding: 'utf8'
	}).trim();
}

/** Sign in as the seeded viewer and land on the WolfBet dashboard with live data. */
export async function signIn(page: Page): Promise<void> {
	await page.goto('/login');
	await page.getByLabel('Email').fill(EMAIL);
	await page.getByLabel('Password').fill(password());
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/wolfbet$/);
	await expect(page.getByRole('banner').getByRole('status')).toHaveText('Live');
}

/** Run docker compose against the e2e project, the same way `tools/e2e_stack.sh` does. */
export function compose(...args: string[]): void {
	execFileSync(resolve(ROOT, 'tools/e2e_stack.sh'), ['compose', ...args], { stdio: 'inherit' });
}
