import { expect, test, type Page } from '@playwright/test';
import { stubApi, viewerMe } from './stub';

test.skip(({ isMobile }) => isMobile, 'the worker is the same at any viewport');

async function rig(page: Page): Promise<string[]> {
	const calls = await stubApi(page, {
		'GET /auth/me': () => ({ status: 200, json: viewerMe }),
		'POST /ws-ticket': () => ({ status: 503, json: { detail: 'offline in tests' } }),
		'GET /wolfbet/ingest': () => ({ status: 200, json: { last_run: null } }),
		'GET /wolfbet/comments': () => ({ status: 200, json: { items: [] } }),
		'GET /wolfbet/series': () => ({ status: 200, json: { res: '1s', series: [] } }),
		'GET /wolfbet/sessions': () => ({ status: 200, json: { items: [], next_cursor: null } })
	});
	return calls;
}

/** Load the app and wait until the service worker controls the page. */
async function controlled(page: Page): Promise<void> {
	await page.goto('/wolfbet/control');
	await page.evaluate(() => navigator.serviceWorker.ready);
	await page.reload();
	await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
}

test('the manifest names the app and its icons load', async ({ page, request }) => {
	await rig(page);
	await page.goto('/login');
	const href = await page.locator('link[rel="manifest"]').getAttribute('href');
	expect(href).toBe('/manifest.webmanifest');
	const response = await request.get('/manifest.webmanifest');
	const manifest = await response.json();
	expect(manifest).toMatchObject({
		name: 'WolfBet dashboard',
		short_name: 'WolfBet',
		start_url: '/',
		display: 'standalone',
		theme_color: '#0f1115',
		background_color: '#0f1115'
	});
	expect(manifest.icons.map((i: { purpose: string }) => i.purpose)).toEqual([
		'any',
		'any',
		'maskable'
	]);
	for (const icon of manifest.icons) {
		const image = await request.get(icon.src);
		expect(image.headers()['content-type']).toBe('image/png');
		const size = await page.evaluate(async (src) => {
			const img = new Image();
			img.src = src;
			await img.decode();
			return `${img.naturalWidth}x${img.naturalHeight}`;
		}, icon.src);
		expect(size).toBe(icon.sizes);
	}
	await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveAttribute(
		'href',
		/\/icons\/apple-touch-icon\.png$/
	);
});

test('Chromium finds the app installable', async ({ page }) => {
	await rig(page);
	await controlled(page);
	const cdp = await page.context().newCDPSession(page);
	const { installabilityErrors } = await cdp.send('Page.getInstallabilityErrors');
	expect(installabilityErrors).toEqual([]);
});

test('api responses never come from the worker or its cache', async ({ page }) => {
	const calls = await rig(page);
	await controlled(page);
	const fromWorker: Record<string, boolean> = {};
	page.on('response', (r) => (fromWorker[new URL(r.url()).pathname] = r.fromServiceWorker()));
	const before = calls.filter((c) => c === 'GET /auth/me').length;
	await page.reload();
	await expect(page.getByRole('heading', { name: 'Control' })).toBeVisible();
	expect(calls.filter((c) => c === 'GET /auth/me').length).toBeGreaterThan(before);
	const api = Object.entries(fromWorker).filter(([path]) => path.startsWith('/api/'));
	expect(api.length).toBeGreaterThan(0);
	expect(api.filter(([, worker]) => worker)).toEqual([]);
	const scripts = Object.entries(fromWorker).filter(([path]) => path.startsWith('/_app/'));
	expect(scripts.length).toBeGreaterThan(0);
	expect(scripts.every(([, worker]) => worker)).toBe(true);
	const cached = await page.evaluate(async () => {
		const names = await caches.keys();
		const urls: string[] = [];
		for (const name of names) {
			for (const req of await (await caches.open(name)).keys()) urls.push(req.url);
		}
		return { names, urls };
	});
	expect(cached.names).toHaveLength(1);
	expect(cached.names[0]).toMatch(/^shell-/);
	expect(cached.urls.some((u) => new URL(u).pathname.startsWith('/api'))).toBe(false);
	expect(cached.urls.some((u) => u.endsWith('/offline.html'))).toBe(true);
});

test('offline, a page shows the offline notice', async ({ page, context }) => {
	await rig(page);
	await controlled(page);
	await context.setOffline(true);
	await page.goto('/wolfbet/analytics');
	await expect(page.getByRole('heading', { name: "You're offline" })).toBeVisible();
	await expect(page.getByText('The dashboard needs a connection to show live data.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Try again' })).toBeVisible();
	await context.setOffline(false);
});
