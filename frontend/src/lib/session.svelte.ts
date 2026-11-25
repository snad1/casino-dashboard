import { goto } from '$app/navigation';
import { resolve } from '$app/paths';
import { createApi, unwrap } from '#lib/api/client';
import type { Me } from '#lib/permissions';
import { SERVICE } from '#lib/service';

export const DEFAULT_SERVICE = SERVICE;

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

const LOGIN_PATH = '/login';
const MAX_UNWRAP = 5;

function nextParam(link: string): string | null {
	const value = /[?&]next=([^&#]*)/.exec(link)?.[1];
	if (value === undefined) return null;
	try {
		return decodeURIComponent(value);
	} catch {
		return null;
	}
}

/** A local path to return to after sign-in; anything else falls back to the dashboard. */
export function safeNext(next: string | null): string {
	// Several requests failing at once on an expired session each build a sign-in link, and the
	// later ones see /login as the current page. Returning there would loop, so follow its own next.
	for (let depth = 0; depth <= MAX_UNWRAP; depth++) {
		if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) {
			break;
		}
		if (next.split(/[?#]/, 1)[0] !== LOGIN_PATH) return next;
		next = nextParam(next);
	}
	return `/${DEFAULT_SERVICE}`;
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
