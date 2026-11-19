import { replaceState } from '$app/navigation';
import { resolve } from '$app/paths';

/**
 * The one-time token from a `#token=...` link, removed from the address bar at once so it
 * never lands in history, a bookmark or a screenshot. The fragment is never sent to a server.
 */
export function tokenFromHash(): string | null {
	const token = new URLSearchParams(location.hash.slice(1)).get('token');
	if (location.hash) {
		const here = resolve('/(app)') + location.pathname.slice(1) + location.search;
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- the current path, only its fragment dropped
		replaceState(here, {});
	}
	return token && token.length >= 20 ? token : null;
}
