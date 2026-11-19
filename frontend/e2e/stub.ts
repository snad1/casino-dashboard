import type { Page, Route } from '@playwright/test';

export type Reply = { status: number; json?: unknown };
export type Handler = (body: unknown, url: URL) => Reply;

export const viewerMe = {
	email: 'viewer@example.test',
	csrf_token: 'csrf-viewer',
	totp_enabled: false,
	roles: [{ service: null, role: 'viewer' }]
};

export const adminMe = {
	email: 'admin@example.test',
	csrf_token: 'csrf-admin',
	totp_enabled: true,
	roles: [{ service: null, role: 'admin' }]
};

/**
 * Answer `/api/*` like the real api, keyed by "METHOD /path" without the `/api` prefix.
 * Anything not listed gets a 404, so an unexpected call fails the test visibly.
 */
export async function stubApi(page: Page, handlers: Record<string, Handler>): Promise<string[]> {
	const calls: string[] = [];
	await page.route('**/api/**', async (route: Route) => {
		const request = route.request();
		const url = new URL(request.url());
		const key = `${request.method()} ${url.pathname.replace(/^\/api/, '')}`;
		calls.push(key);
		const handler = handlers[key];
		const body = request.postData() ? JSON.parse(request.postData() as string) : null;
		const reply = handler ? handler(body, url) : { status: 404, json: { detail: 'Not found' } };
		await route.fulfill({
			status: reply.status,
			contentType: 'application/json',
			body: reply.json === undefined ? '' : JSON.stringify(reply.json)
		});
	});
	return calls;
}
