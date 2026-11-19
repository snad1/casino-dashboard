import { defineConfig, devices } from '@playwright/test';

/** Frame-rate and memory checks against a production build with a simulated feed; not in CI. */
export default defineConfig({
	testDir: 'perf',
	workers: 1,
	timeout: 0,
	webServer: {
		command: 'pnpm build && pnpm preview --port 4174 --strictPort',
		port: 4174,
		reuseExistingServer: false
	},
	use: { baseURL: 'http://localhost:4174', ...devices['Desktop Chrome'] }
});
