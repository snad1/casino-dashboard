export const OFFLINE_PATH = '/offline.html';

export type Route = 'skip' | 'cache' | 'navigate';

/**
 * How the service worker handles one request. Anything from the api or the live socket is
 * `skip`: it goes straight to the network and never touches a cache, so no account data or
 * money figures are ever stored or shown stale. Only the build's own files are cached.
 */
export function route(
	url: URL,
	method: string,
	mode: RequestMode,
	assets: ReadonlySet<string>,
	origin: string
): Route {
	if (method !== 'GET' || url.origin !== origin) return 'skip';
	const path = url.pathname;
	if (path === '/api' || path.startsWith('/api/') || path === '/ws' || path.startsWith('/ws/')) {
		return 'skip';
	}
	if (assets.has(path)) return 'cache';
	if (mode === 'navigate') return 'navigate';
	return 'skip';
}
