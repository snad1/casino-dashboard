import { resolve } from '$app/paths';
import type { components } from '#lib/api/schema';
import { unwrap } from '#lib/api/client';
import { api } from '#lib/session.svelte';

type SessionPage = components['schemas']['SessionPage'];

export type SeriesQuery = { metric: 'profit' | 'balance'; from: string; to: string; lane?: number };
export type SessionsQuery = {
	limit: number;
	since?: string;
	until?: string;
	status?: 'running' | 'ended';
	cursor?: string;
	lane?: number;
};

const serviceSeries = (service: string, query: SeriesQuery & { epoch?: number }) =>
	unwrap(api.GET('/{service}/series', { params: { path: { service }, query } }));
type SeriesOut = Awaited<ReturnType<typeof serviceSeries>>;

/**
 * Where the chart and the sessions list read history from: a whole service, or one Telegram
 * user's own bets. A Telegram user has one lane, so the lane filter does not apply there.
 * `live` is false for a database a reset archived: live ticks belong to the running bot.
 */
export interface HistorySource {
	live: boolean;
	series(query: SeriesQuery): Promise<SeriesOut>;
	sessions(query: SessionsQuery): Promise<SessionPage>;
	sessionHref(epoch: number, id: number): string;
}

/** `epoch` picks a database a reset archived; without it, the one since the last reset. */
export function serviceSource(service: string, epoch: number | null = null): HistorySource {
	const picked = epoch === null ? {} : { epoch };
	return {
		live: epoch === null,
		series: (query) => serviceSeries(service, { ...query, ...picked }),
		sessions: (query) =>
			unwrap(
				api.GET('/{service}/sessions', {
					params: { path: { service }, query: { ...query, ...picked } }
				})
			),
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
		live: true,
		series: ({ metric, from, to }) =>
			unwrap(api.GET('/tg/{uid}/series', { params: { path, query: { metric, from, to } } })),
		sessions: ({ limit, since, until, status, cursor }) =>
			unwrap(
				api.GET('/tg/{uid}/sessions', {
					params: { path, query: { limit, since, until, status, cursor } }
				})
			),
		sessionHref: (epoch, id) =>
			resolve('/(app)/tg/[uid=uid]/sessions/[epoch]/[id]', {
				uid: String(uid),
				epoch: String(epoch),
				id: String(id)
			})
	};
}
