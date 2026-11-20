/** Turning push notifications on and off for this browser, and reading what it supports. */

export type PushSupport = 'unsupported' | 'blocked' | 'ready';

export interface PushApi {
	add: (body: { endpoint: string; keys: { p256dh: string; auth: string } }) => Promise<void>;
	remove: (endpoint: string) => Promise<void>;
}

export interface PushEnv {
	serviceWorker?: ServiceWorkerContainer;
	PushManager?: unknown;
	Notification?: {
		permission: NotificationPermission;
		requestPermission(): Promise<NotificationPermission>;
	};
}

function env(): PushEnv {
	return {
		serviceWorker: 'serviceWorker' in navigator ? navigator.serviceWorker : undefined,
		PushManager: 'PushManager' in window ? window.PushManager : undefined,
		Notification: 'Notification' in window ? window.Notification : undefined
	};
}

/** Whether this browser can show push notifications for the dashboard. */
export function pushSupport(e: PushEnv = env()): PushSupport {
	if (!e.serviceWorker || !e.PushManager || !e.Notification) return 'unsupported';
	return e.Notification.permission === 'denied' ? 'blocked' : 'ready';
}

/** A base64url key as the bytes `pushManager.subscribe` wants. */
export function urlBase64ToBytes(value: string): Uint8Array<ArrayBuffer> {
	const base64 = (value + '='.repeat((4 - (value.length % 4)) % 4))
		.replace(/-/g, '+')
		.replace(/_/g, '/');
	const raw = atob(base64);
	const bytes = new Uint8Array(raw.length);
	for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
	return bytes;
}

function keysOf(subscription: PushSubscription): { p256dh: string; auth: string } {
	const json = subscription.toJSON();
	const keys = json.keys ?? {};
	if (!keys.p256dh || !keys.auth) throw new Error('This browser gave no push keys.');
	return { p256dh: keys.p256dh, auth: keys.auth };
}

/** Whether this browser already sends this dashboard's notifications. */
export async function deviceSubscribed(e: PushEnv = env()): Promise<boolean> {
	if (pushSupport(e) === 'unsupported' || !e.serviceWorker) return false;
	const registration = await e.serviceWorker.ready;
	return (await registration.pushManager.getSubscription()) !== null;
}

/**
 * Ask for permission (only ever from a button press), subscribe this browser with the
 * dashboard's public key and register it. Returns false when the user said no.
 */
export async function subscribe(key: string, api: PushApi, e: PushEnv = env()): Promise<boolean> {
	if (pushSupport(e) !== 'ready' || !e.serviceWorker || !e.Notification) return false;
	if ((await e.Notification.requestPermission()) !== 'granted') return false;
	const registration = await e.serviceWorker.ready;
	const subscription =
		(await registration.pushManager.getSubscription()) ??
		(await registration.pushManager.subscribe({
			userVisibleOnly: true,
			applicationServerKey: urlBase64ToBytes(key)
		}));
	await api.add({ endpoint: subscription.endpoint, keys: keysOf(subscription) });
	return true;
}

/** Stop this browser's notifications, here and on the dashboard. */
export async function unsubscribe(api: PushApi, e: PushEnv = env()): Promise<void> {
	if (!e.serviceWorker) return;
	const registration = await e.serviceWorker.ready;
	const subscription = await registration.pushManager.getSubscription();
	if (!subscription) return;
	await api.remove(subscription.endpoint);
	await subscription.unsubscribe();
}

/** What the service worker shows for a push: the payload's text, never markup. */
export function notificationFrom(
	data: unknown,
	origin: string
): { title: string; body: string; url: string } {
	const fallback = { title: 'Casino dashboard', body: 'New alert', url: '/alerts' };
	if (typeof data !== 'object' || data === null) return fallback;
	const doc = data as Record<string, unknown>;
	const text = (v: unknown, d: string, max: number) =>
		typeof v === 'string' && v.trim() ? v.slice(0, max) : d;
	return {
		title: text(doc.title, fallback.title, 80),
		body: text(doc.body, fallback.body, 500),
		url: samePath(doc.url, origin) ?? fallback.url
	};
}

/** A path on this site, or null for anything that would leave it. */
export function samePath(url: unknown, origin: string): string | null {
	if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith('//')) return null;
	const resolved = new URL(url, origin);
	return resolved.origin === origin ? resolved.pathname + resolved.search : null;
}
