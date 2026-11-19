import { goto } from '$app/navigation';
import { resolve } from '$app/paths';
import { createApi, unwrap } from '#lib/api/client';
import type { Me } from '#lib/permissions';

export const DEFAULT_SERVICE = 'wolfbet';

/** The signed-in user, as the api reported it; null when signed out or not loaded yet. */
export const session = $state<{ me: Me | null }>({ me: null });

export const api = createApi({
	csrf: () => session.me?.csrf_token ?? null,
	onUnauthorized: () => {
		session.me = null;
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- a resolved path plus its ?next= query
		void goto(resolve('/(public)/login') + loginQuery(location.pathname + location.search));
	}
});

/** Sign-in page URL that returns here afterwards; only same-site paths are kept. */
export function loginUrl(next: string): string {
	return resolve('/(public)/login') + loginQuery(next);
}

function loginQuery(next: string): string {
	return `?next=${encodeURIComponent(safeNext(next))}`;
}

/** A local path to return to after sign-in; anything else falls back to the dashboard. */
export function safeNext(next: string | null): string {
	if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) {
		return `/${DEFAULT_SERVICE}`;
	}
	return next;
}

/** Load the current user; returns null when there is no valid session. */
export async function loadMe(): Promise<Me | null> {
	const { data, response } = await api.GET('/auth/me');
	session.me = response.ok && data ? data : null;
	return session.me;
}

export async function signOut(): Promise<void> {
	try {
		await unwrap(api.POST('/auth/logout'));
	} finally {
		session.me = null;
		await goto(resolve('/(public)/login'));
	}
}
