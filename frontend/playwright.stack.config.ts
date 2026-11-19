import { defineConfig, devices } from '@playwright/test';

/** The real stack from `tools/e2e_stack.sh up`: Caddy, api, Timescale, Redis, replayed bot events. */
export default defineConfig({
	testDir: 'e2e-stack',
	workers: 1,
	timeout: 60_000,
	expect: { timeout: 15_000 },
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
	use: {
		baseURL: process.env.E2E_BASE_URL ?? 'https://localhost:8443',
		ignoreHTTPSErrors: true,
		trace: 'retain-on-failure'
	},
	projects: [
		{ name: 'desktop', use: { ...devices['Desktop Chrome'] } },
		{ name: 'mobile', use: { ...devices['Pixel 7'] } }
	]
});
