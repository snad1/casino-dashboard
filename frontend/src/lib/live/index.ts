import { resolve } from '$app/paths';
import { ApiError, unwrap } from '#lib/api/client';
import { api, loginUrl } from '#lib/session.svelte';
import { LiveStore, SignedOut } from './store.svelte';

const stores = new Map<string, LiveStore>();

async function getTicket(): Promise<string> {
	try {
		return (await unwrap(api.POST('/ws-ticket'))).ticket;
	} catch (error) {
		if (error instanceof ApiError && error.status === 401) throw new SignedOut();
		throw error;
	}
}

function socketUrl(ticket: string): string {
	const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
	return `${scheme}://${location.host}${resolve('/(app)')}ws?ticket=${encodeURIComponent(ticket)}`;
}

/** The one live connection for `service`, shared by the header and the dashboard panels. */
export function liveFor(service: string): LiveStore {
	let store = stores.get(service);
	if (!store) {
		store = new LiveStore({
			getTicket,
			openSocket: (ticket) => new WebSocket(socketUrl(ticket)),
			onSignedOut: () => location.assign(loginUrl(location.pathname + location.search))
		});
		stores.set(service, store);
	}
	return store;
}
