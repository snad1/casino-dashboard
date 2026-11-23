<script lang="ts">
	import { onMount } from 'svelte';
	import type { components } from '#lib/api/schema';
	import { formatProfit } from '#lib/format';
	import { serviceSource, type HistorySource, type SessionsQuery } from '#lib/history-source';
	import type { LiveStore } from '#lib/live/store.svelte';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';
	import VirtualList from '#ui/VirtualList';

	type Session = components['schemas']['SessionOut'];
	type Row = {
		key: string;
		lane: number;
		id: number;
		epoch: number | null;
		live: boolean;
		bets: number;
		profit: number;
		currency: string | null;
		started: string | null;
		game: string | null;
		strategy: string | null;
	};
	type Filters = Omit<SessionsQuery, 'limit' | 'cursor'>;

	let {
		live,
		service,
		source = serviceSource(service),
		empty = 'No sessions yet.',
		filters = {},
		ontotal
	}: {
		live: LiveStore;
		service: string;
		source?: HistorySource;
		empty?: string;
		filters?: Filters;
		ontotal?: (total: number) => void;
	} = $props();

	const showsLive = $derived(
		source.live && filters.status !== 'ended' && !filters.since && !filters.until
	);

	const ROW = 44;
	const MAX_ROWS = 2000;
	const LOAD_FAILED = "Couldn't load sessions. Try again.";
	let history: Session[] = $state.raw([]);
	let cursor: string | null = null;
	let done = $state(false);
	let busy = false;
	let error = $state<string | null>(null);

	const liveRows = $derived(
		Object.entries(showsLive ? live.lanes : {})
			.filter(([, lane]) => typeof lane.session_id === 'number')
			.filter(([n]) => filters.lane === undefined || Number(n) === filters.lane)
			.map(([n, lane]): Row => {
				const id = lane.session_id as number;
				const stored = history.find((s) => s.id === id && s.lane === Number(n) && !s.ended_at);
				return {
					key: `live-${n}`,
					lane: Number(n),
					id,
					epoch: stored?.epoch_id ?? null,
					live: true,
					bets: lane.total_bets ?? 0,
					profit: lane.profit ?? 0,
					currency: lane.currency ?? null,
					started: stored?.started_at ?? null,
					game: stored?.game ?? null,
					strategy: stored?.strategy ?? null
				};
			})
			.sort((a, b) => a.lane - b.lane)
	);
	const rows = $derived([
		...liveRows,
		...history
			.filter((s) => !liveRows.some((l) => l.id === s.id && l.lane === s.lane && !s.ended_at))
			.map((s): Row => ({
				key: `${s.epoch_id}-${s.id}`,
				lane: s.lane,
				id: s.id,
				epoch: s.epoch_id,
				live: false,
				bets: s.total_bets ?? 0,
				profit: s.profit ?? 0,
				currency: s.currency,
				started: s.started_at,
				game: s.game,
				strategy: s.strategy
			}))
	]);

	async function more() {
		if (busy || done) return;
		busy = true;
		error = null;
		try {
			const page = await source.sessions({ limit: 50, ...filters, ...(cursor ? { cursor } : {}) });
			if (!cursor && typeof page.total === 'number') ontotal?.(page.total);
			history = [...history, ...page.items].slice(0, MAX_ROWS);
			cursor = page.next_cursor ?? null;
			done = cursor === null || history.length >= MAX_ROWS;
		} catch {
			error = LOAD_FAILED;
		} finally {
			busy = false;
		}
	}

	/** Re-reads the newest page so a session that ended since the page loaded stays listed. */
	async function refreshHead() {
		try {
			const page = await source.sessions({ limit: 50, ...filters });
			if (typeof page.total === 'number') ontotal?.(page.total);
			const fresh = new Set(page.items.map((s) => `${s.epoch_id}-${s.id}`));
			const older = history.filter((s) => !fresh.has(`${s.epoch_id}-${s.id}`));
			history = [...page.items, ...older].slice(0, MAX_ROWS);
		} catch {
			error = LOAD_FAILED;
		}
	}

	const liveIds = $derived(liveRows.map((r) => `${r.lane}:${r.id}`).join(','));
	let seenIds = '';
	$effect(() => {
		const ids = liveIds;
		if (seenIds && ids !== seenIds) void refreshHead();
		seenIds = ids;
	});

	onMount(more);

	const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '');
</script>

<div class="flex h-full flex-col gap-2">
	{#if error}
		<Notice tone="error">{error}</Notice>
		<Button onclick={more}>Try again</Button>
	{/if}
	{#if done && !error && rows.length === 0}
		<p class="text-sm text-muted">{empty}</p>
	{/if}
	<div class="min-h-0 flex-1">
		<VirtualList items={rows} key={(r) => r.key} rowHeight={ROW} label="Sessions" onend={more}>
			{#snippet row(s: Row)}
				{@const href = s.epoch === null ? null : source.sessionHref(s.epoch, s.id)}
				<div
					role="row"
					class="flex h-full items-center justify-between gap-2 border-b border-line text-sm"
				>
					<span role="cell" class="flex flex-col">
						{#if href}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- the source builds it with resolve() -->
							<a {href} class="text-ink underline-offset-2 hover:underline"
								>#{s.id} · Lane {s.lane}</a
							>
						{:else}
							<span>#{s.id} · Lane {s.lane}</span>
						{/if}
						<span class="text-xs text-muted"
							>{[s.live ? 'Live' : when(s.started), `${s.bets} bets`, s.game, s.strategy]
								.filter(Boolean)
								.join(' · ')}</span
						>
					</span>
					<span
						role="cell"
						class="font-mono text-xs tabular-nums {s.profit < 0 ? 'text-loss' : 'text-profit'}"
						>{formatProfit(s.profit)}</span
					>
				</div>
			{/snippet}
		</VirtualList>
	</div>
</div>
