/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />
import { version } from '$app/env';
import { assets, immutable, prerendered } from '$app/manifest';
import { self as sw } from '$app/service-worker';
import { OFFLINE_PATH, route } from '#lib/offline';
import { notificationFrom, samePath } from '#lib/push';

const PREFIX = 'shell-';
const CACHE = `${PREFIX}${version}`;
const ASSETS = new Set(
	[...immutable, ...assets, ...prerendered].map(({ path }) => `/${path.replace(/^\//, '')}`)
);

sw.addEventListener('install', (event) => {
	event.waitUntil(
		caches
			.open(CACHE)
			.then((cache) => cache.addAll([...ASSETS]))
			.then(() => sw.skipWaiting())
	);
});

sw.addEventListener('activate', (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(
					keys.filter((k) => k.startsWith(PREFIX) && k !== CACHE).map((k) => caches.delete(k))
				)
			)
			.then(() => sw.clients.claim())
	);
});

sw.addEventListener('fetch', (event) => {
	const { request } = event;
	const url = new URL(request.url);
	const kind = route(url, request.method, request.mode, ASSETS, sw.location.origin);
	if (kind === 'cache') {
		event.respondWith(
			caches.match(url.pathname, { cacheName: CACHE }).then((hit) => hit ?? fetch(request))
		);
	} else if (kind === 'navigate') {
		event.respondWith(
			fetch(request).catch(async () => {
				const page = await caches.match(OFFLINE_PATH, { cacheName: CACHE });
				return page ?? Response.error();
			})
		);
	}
});

function readPush(event: PushEvent): unknown {
	try {
		return event.data?.json() ?? null;
	} catch {
		return null;
	}
}

sw.addEventListener('push', (event) => {
	const note = notificationFrom(readPush(event), sw.location.origin);
	event.waitUntil(
		sw.registration.showNotification(note.title, {
			body: note.body,
			data: { url: note.url },
			tag: 'alert'
		})
	);
});

sw.addEventListener('notificationclick', (event) => {
	event.notification.close();
	const url =
		samePath((event.notification.data as { url?: unknown } | null)?.url, sw.location.origin) ??
		'/alerts';
	event.waitUntil(
		sw.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (windows) => {
			const open = windows.find((w) => new URL(w.url).origin === sw.location.origin);
			if (open) {
				await open.navigate(url);
				await open.focus();
			} else {
				await sw.clients.openWindow(url);
			}
		})
	);
});
