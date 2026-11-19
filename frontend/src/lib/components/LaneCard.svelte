<script lang="ts">
	import { decimalsFor, formatAmount, formatProfit } from '#lib/format';
	import type { LaneFields, LaneStatus } from '#lib/live/protocol';

	let { n, lane }: { n: number; lane: LaneFields } = $props();

	const WORDS: Record<LaneStatus, string> = {
		running: 'Running',
		paused: 'Paused',
		stopped: 'Stopped',
		stale: 'Stale',
		down: 'Down',
		events_off: 'Events off'
	};
	const GOOD = new Set<LaneStatus>(['running', 'paused']);

	const status = $derived(lane.status ?? 'stale');
	const currency = $derived(lane.currency ?? null);
	const profit = $derived(lane.profit ?? 0);
	const streak = $derived(lane.current_streak ?? 0);
	const money = (value: number | null | undefined) =>
		typeof value === 'number' ? formatAmount(value, currency) : 'n/a';
	const facts = $derived([
		['Session', lane.session_id ? `#${lane.session_id}` : 'none'],
		['Bets', String(lane.total_bets ?? 0)],
		['bps', String(lane.bps_1s ?? 0)],
		['Streak', streak > 0 ? `W${streak}` : streak < 0 ? `L${-streak}` : '0'],
		['Base bet', money(lane.base_bet)],
		['Current bet', money(lane.current_bet)],
		['Split', lane.lane_split ?? 'n/a'],
		...(typeof lane.run_profit === 'number'
			? [['Run profit', formatProfit(lane.run_profit, decimalsFor(currency))]]
			: [])
	]);
</script>

<article
	class="flex flex-col gap-2 rounded-lg border border-line bg-raised p-3 text-sm"
	aria-labelledby="lane-{n}-title"
>
	<header class="flex items-center justify-between">
		<h3 id="lane-{n}-title" class="font-semibold">Lane {n}</h3>
		<span
			class="rounded-sm border px-2 py-0.5 text-xs {GOOD.has(status)
				? 'border-line text-muted'
				: 'border-warn text-warn'}">{WORDS[status]}</span
		>
	</header>
	<p
		class="font-mono text-lg font-semibold tabular-nums {profit < 0 ? 'text-loss' : 'text-profit'}"
	>
		<span aria-hidden="true">{profit < 0 ? '▼' : '▲'}</span>
		{formatProfit(profit, decimalsFor(currency))}
		<span class="text-xs font-normal text-muted">{currency ?? ''}</span>
	</p>
	<dl class="grid grid-cols-2 gap-x-3 gap-y-1">
		{#each facts as [label, value] (label)}
			<div class="flex justify-between gap-2">
				<dt class="text-muted">{label}</dt>
				<dd class="font-mono tabular-nums">{value}</dd>
			</div>
		{/each}
	</dl>
	{#if lane.stop_reason}
		<p class="text-xs text-warn">Stopped: {lane.stop_reason}</p>
	{/if}
</article>
