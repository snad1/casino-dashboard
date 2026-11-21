import { describe, expect, it, vi } from 'vitest';
import { ApiError, NETWORK_ERROR, createApi, unwrap } from './client';

const BASE = 'http://dash.test/api';

function harness(status = 204, body: unknown = null) {
	const seen: Request[] = [];
	const onUnauthorized = vi.fn();
	const fetch = vi.fn(async (input: Request) => {
		seen.push(input);
		const payload = body === null ? null : JSON.stringify(body);
		return new Response(payload, {
			status,
			headers: payload ? { 'content-type': 'application/json' } : {}
		});
	});
	const api = createApi({
		baseUrl: BASE,
		fetch: fetch as unknown as typeof globalThis.fetch,
		csrf: () => 'token-123',
		onUnauthorized
	});
	return { api, seen, onUnauthorized };
}

describe('api client', () => {
	it('sends the CSRF token on state-changing requests only', async () => {
		const { api, seen } = harness(204);
		await api.POST('/auth/logout');
		await api.GET('/auth/me');
		expect(seen[0]?.headers.get('X-CSRF-Token')).toBe('token-123');
		expect(seen[1]?.headers.has('X-CSRF-Token')).toBe(false);
	});

	it('hands a 401 to the caller so it can go to sign-in', async () => {
		const { api, onUnauthorized } = harness(401, { detail: 'Sign in to continue.' });
		await api.GET('/admin/users');
		expect(onUnauthorized).toHaveBeenCalledOnce();
	});

	it('leaves a 401 from either sign-in or the session check to the page', async () => {
		const { api, onUnauthorized } = harness(401, { detail: 'nope' });
		await api.POST('/auth/login', { body: { email: 'a@b.test', password: 'x', code: null } });
		await api.POST('/auth/telegram', { body: { token: 'x' } });
		await api.GET('/auth/me');
		expect(onUnauthorized).not.toHaveBeenCalled();
	});

	it('carries the next-step flag and the message to show', async () => {
		const body = { detail: 'Enter the code from your authenticator app.', totp_required: true };
		const { api } = harness(401, body);
		const error = await unwrap(
			api.POST('/auth/login', { body: { email: 'a@b.test', password: 'x', code: null } })
		).catch((e: unknown) => e);
		expect(error).toBeInstanceOf(ApiError);
		expect((error as ApiError).flags.has('totp_required')).toBe(true);
		expect((error as ApiError).detail).toBe(body.detail);
	});

	it('turns a validation error or a dropped connection into a plain message', async () => {
		const { api } = harness(422, { detail: [{ loc: ['body', 'email'], msg: 'bad' }] });
		const invalid = (await unwrap(api.GET('/admin/users')).catch((e: unknown) => e)) as ApiError;
		expect(invalid.detail).toBe('Some of the values are not valid. Check them and try again.');
		const offline = (await unwrap(Promise.reject(new TypeError('Failed to fetch'))).catch(
			(e: unknown) => e
		)) as ApiError;
		expect(offline.detail).toBe(NETWORK_ERROR);
	});
});
