import createClient from 'openapi-fetch';
import type { paths } from './schema';

export const NETWORK_ERROR =
	"Can't reach the dashboard right now. Check your connection and try again.";
const GENERIC = 'Something went wrong. Please try again.';
const INVALID = 'Some of the values are not valid. Check them and try again.';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const NO_REDIRECT = ['/auth/login', '/auth/me'];

/** An api failure with the user-safe message to show and any next-step flags it carried. */
export class ApiError extends Error {
	constructor(
		readonly status: number,
		readonly detail: string,
		readonly flags: ReadonlySet<string> = new Set()
	) {
		super(detail);
	}
}

export interface ApiOptions {
	baseUrl?: string;
	fetch?: typeof fetch;
	csrf: () => string | null;
	onUnauthorized: () => void;
}

/**
 * The typed client for every api call. The session lives only in the HttpOnly cookie; this
 * adds the session's CSRF token to state-changing requests and hands a 401 to the caller,
 * except for the sign-in and session checks, which handle it themselves.
 */
export function createApi(options: ApiOptions) {
	const baseUrl = options.baseUrl ?? '/api';
	const basePath = new URL(baseUrl, 'http://local').pathname;
	const client = createClient<paths>({
		baseUrl,
		fetch: options.fetch,
		credentials: 'same-origin'
	});
	client.use({
		onRequest({ request }) {
			const token = options.csrf();
			if (!SAFE_METHODS.has(request.method) && token) {
				request.headers.set('X-CSRF-Token', token);
			}
			return request;
		},
		onResponse({ request, response }) {
			const path = new URL(request.url, 'http://local').pathname.slice(basePath.length);
			if (response.status === 401 && !NO_REDIRECT.some((p) => path.startsWith(p))) {
				options.onUnauthorized();
			}
			return response;
		}
	});
	return client;
}

export type Api = ReturnType<typeof createApi>;

function toApiError(status: number, error: unknown): ApiError {
	if (typeof error !== 'object' || error === null) return new ApiError(status, GENERIC);
	const body = error as Record<string, unknown>;
	const flags = new Set(Object.keys(body).filter((key) => body[key] === true));
	if (typeof body.detail === 'string') return new ApiError(status, body.detail, flags);
	if (Array.isArray(body.detail)) return new ApiError(status, INVALID, flags);
	return new ApiError(status, GENERIC, flags);
}

/** Await an api call and return its data, or throw an `ApiError` with a message to show. */
export async function unwrap<T>(
	call: Promise<{ data?: T; error?: unknown; response: Response }>
): Promise<T> {
	let result: { data?: T; error?: unknown; response: Response };
	try {
		result = await call;
	} catch {
		throw new ApiError(0, NETWORK_ERROR);
	}
	if (!result.response.ok) throw toApiError(result.response.status, result.error);
	return result.data as T;
}
