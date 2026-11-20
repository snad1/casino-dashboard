import { describe, expect, it, vi } from 'vitest';
import {
	notificationFrom,
	pushSupport,
	samePath,
	subscribe,
	unsubscribe,
	urlBase64ToBytes,
	type PushEnv
} from './push';

const ORIGIN = 'https://dash.example.test';

function fakeEnv(permission: NotificationPermission, grant: NotificationPermission = 'granted') {
	let current: {
		endpoint: string;
		unsubscribe: () => Promise<boolean>;
		toJSON: () => unknown;
	} | null = null;
	const created = {
		endpoint: 'https://fcm.googleapis.com/fcm/send/abc',
		unsubscribe: vi.fn(async () => {
			current = null;
			return true;
		}),
		toJSON: () => ({ endpoint: created.endpoint, keys: { p256dh: 'p-key', auth: 'a-key' } })
	};
	const pushManager = {
		getSubscription: vi.fn(async () => current),
		subscribe: vi.fn(async () => {
			current = created;
			return created;
		})
	};
	const env: PushEnv = {
		serviceWorker: { ready: Promise.resolve({ pushManager }) } as unknown as ServiceWorkerContainer,
		PushManager: {},
		Notification: { permission, requestPermission: vi.fn(async () => grant) }
	};
	const api = { add: vi.fn(async () => undefined), remove: vi.fn(async () => undefined) };
	return { env, api, pushManager, created };
}

describe('push support', () => {
	it('tells unsupported, blocked and ready apart', () => {
		expect(pushSupport({})).toBe('unsupported');
		expect(pushSupport(fakeEnv('denied').env)).toBe('blocked');
		expect(pushSupport(fakeEnv('default').env)).toBe('ready');
	});

	it('decodes the base64url public key', () => {
		expect([...urlBase64ToBytes('BAE')]).toEqual([4, 1]);
		expect([...urlBase64ToBytes('-_8')]).toEqual([251, 255]);
	});
});

describe('subscribing', () => {
	it('asks first, subscribes with the key and registers the browser', async () => {
		const { env, api, pushManager } = fakeEnv('default');
		expect(await subscribe('BAE', api, env)).toBe(true);
		const options = pushManager.subscribe.mock.calls[0] as unknown as [PushSubscriptionOptionsInit];
		expect(options[0].userVisibleOnly).toBe(true);
		expect(api.add).toHaveBeenCalledWith({
			endpoint: 'https://fcm.googleapis.com/fcm/send/abc',
			keys: { p256dh: 'p-key', auth: 'a-key' }
		});
	});

	it('does nothing when the user says no', async () => {
		const { env, api, pushManager } = fakeEnv('default', 'denied');
		expect(await subscribe('BAE', api, env)).toBe(false);
		expect(pushManager.subscribe).not.toHaveBeenCalled();
		expect(api.add).not.toHaveBeenCalled();
	});

	it('unsubscribes here and on the dashboard', async () => {
		const { env, api, created } = fakeEnv('default');
		await subscribe('BAE', api, env);
		await unsubscribe(api, env);
		expect(api.remove).toHaveBeenCalledWith(created.endpoint);
		expect(created.unsubscribe).toHaveBeenCalled();
	});
});

describe('notifications', () => {
	it('shows the payload text and falls back on anything odd', () => {
		expect(notificationFrom({ title: 'T', body: 'B', url: '/alerts?x=1' }, ORIGIN)).toEqual({
			title: 'T',
			body: 'B',
			url: '/alerts?x=1'
		});
		expect(notificationFrom('nope', ORIGIN)).toEqual({
			title: 'Casino dashboard',
			body: 'New alert',
			url: '/alerts'
		});
		expect(notificationFrom({ body: 'x'.repeat(900) }, ORIGIN).body).toHaveLength(500);
	});

	it('only opens paths on this site', () => {
		expect(samePath('/alerts', ORIGIN)).toBe('/alerts');
		expect(samePath('https://evil.example/', ORIGIN)).toBeNull();
		expect(samePath('//evil.example/x', ORIGIN)).toBeNull();
		expect(samePath('javascript:alert(1)', ORIGIN)).toBeNull();
		expect(samePath(42, ORIGIN)).toBeNull();
	});
});
