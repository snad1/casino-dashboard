import { defineConfig, devices } from '@playwright/test';
import { STATE } from './e2e-stack/stack';

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
		{ name: 'setup', testMatch: /auth\.setup\.ts/ },
		{
			name: 'desktop',
			dependencies: ['setup'],
			use: { ...devices['Desktop Chrome'], storageState: STATE.viewer }
		},
		{
			name: 'mobile',
			dependencies: ['setup'],
			use: { ...devices['Pixel 7'], storageState: STATE.viewer }
		}
	]
});
