import { resolve } from '$app/paths';
import type { components } from '#lib/api/schema';
import { unwrap } from '#lib/api/client';
import { api } from '#lib/session.svelte';

type SessionPage = components['schemas']['SessionPage'];

export type SeriesQuery = { metric: 'profit' | 'balance'; from: string; to: string; lane?: number };
export type SessionsQuery = { limit: number; since?: string; cursor?: string; lane?: number };

const serviceSeries = (service: string, query: SeriesQuery) =>
	unwrap(api.GET('/{service}/series', { params: { path: { service }, query } }));
type SeriesOut = Awaited<ReturnType<typeof serviceSeries>>;

/**
 * Where the chart and the sessions list read history from: a whole service, or one Telegram
 * user's own bets. A Telegram user has one lane, so the lane filter does not apply there.
 */
export interface HistorySource {
	series(query: SeriesQuery): Promise<SeriesOut>;
	sessions(query: SessionsQuery): Promise<SessionPage>;
	sessionHref(epoch: number, id: number): string;
}

export function serviceSource(service: string): HistorySource {
	return {
		series: (query) => serviceSeries(service, query),
		sessions: (query) =>
			unwrap(api.GET('/{service}/sessions', { params: { path: { service }, query } })),
		sessionHref: (epoch, id) =>
			resolve('/(app)/[service=service]/sessions/[epoch]/[id]', {
				service,
				epoch: String(epoch),
				id: String(id)
			})
	};
}

export function tgSource(uid: number): HistorySource {
	const path = { uid };
	return {
		series: ({ metric, from, to }) =>
			unwrap(api.GET('/tg/{uid}/series', { params: { path, query: { metric, from, to } } })),
		sessions: ({ limit, since, cursor }) =>
			unwrap(api.GET('/tg/{uid}/sessions', { params: { path, query: { limit, since, cursor } } })),
		sessionHref: (epoch, id) =>
			resolve('/(app)/tg/[uid=uid]/sessions/[epoch]/[id]', {
				uid: String(uid),
				epoch: String(epoch),
				id: String(id)
			})
	};
}
