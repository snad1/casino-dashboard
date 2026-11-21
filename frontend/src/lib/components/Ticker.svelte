<script lang="ts">
	import { formatAmount, formatProfit } from '#lib/format';
	import type { LaneStatus } from '#lib/live/protocol';
	import type { LiveStore } from '#lib/live/store.svelte';

	let { live, service }: { live: LiveStore; service: string } = $props();

	const STATUS_WORDS: Record<LaneStatus, string> = {
		running: 'Running',
		paused: 'Paused',
		stopped: 'Stopped',
		stale: 'Stale',
		down: 'Down',
		events_off: 'Events off'
	};
	const ATTENTION = new Set<LaneStatus>(['stale', 'down', 'events_off']);

	const lanes = $derived(
		Object.entries(live.lanes)
			.map(([n, lane]) => ({ n: Number(n), ...lane }))
			.sort((a, b) => a.n - b.n)
	);
	const sum = (key: 'profit' | 'total_bets' | 'wins' | 'losses' | 'wagered' | 'bps_1s') =>
		lanes.reduce((total, lane) => total + (lane[key] ?? 0), 0);
	const worst = (key: 'api_ms_p50' | 'api_ms_p99') => {
		const values = lanes.map((lane) => lane[key]).filter((v): v is number => typeof v === 'number');
		return values.length ? Math.max(...values) : null;
	};

	const profit = $derived(sum('profit'));
	const wagered = $derived(sum('wagered'));
	const roi = $derived(wagered > 0 ? (profit / wagered) * 100 : 0);
	const balanced = $derived(lanes.filter((lane) => typeof lane.balance === 'number'));
	const balance = $derived(
		balanced.length ? Math.max(...balanced.map((lane) => lane.balance as number)) : null
	);
	const currency = $derived(balanced.find((lane) => lane.balance === balance)?.currency ?? '');
	const p50 = $derived(worst('api_ms_p50'));
	const p99 = $derived(worst('api_ms_p99'));

	let previous: number | null = null;
	let direction: 'up' | 'down' | null = $state(null);
	$effect(() => {
		if (previous !== null && profit !== previous) direction = profit > previous ? 'up' : 'down';
		previous = profit;
	});

	const link = $derived.by(() => {
		if (live.connection === 'reconnecting') return 'Reconnecting';
		if (live.connection === 'connecting' || live.connection === 'idle') return 'Connecting';
		if (live.connection === 'signed_out') return 'Signed out';
		return live.stale ? 'Stale' : 'Live';
	});
</script>

<!-- A scrollable region must be reachable by keyboard so its overflow can be scrolled. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	role="group"
	aria-label="Live totals"
	tabindex="0"
	class="flex min-w-0 flex-1 items-center gap-4 overflow-x-auto text-sm focus-visible:outline-2 focus-visible:outline-focus"
>
	<span class="font-semibold">{service}</span>
	<span
		class="rounded-sm px-2 py-0.5 text-xs {link === 'Live'
			? 'bg-raised text-profit'
			: 'bg-raised text-warn'}"
		role="status"
		aria-live="polite">{link}</span
	>
	{#key profit}
		<span
			class="font-mono font-semibold tabular-nums {profit < 0 ? 'text-loss' : 'text-profit'}"
			class:flash-up={direction === 'up'}
			class:flash-down={direction === 'down'}
			aria-label="Session profit {formatProfit(profit)}"
		>
			<span aria-hidden="true">{profit < 0 ? '▼' : '▲'}</span>
			{formatProfit(profit)}
		</span>
	{/key}
	<span class="text-muted"
		>Bets <span class="font-mono text-ink tabular-nums">{sum('total_bets')}</span></span
	>
	<span class="text-muted"
		>W/L <span class="font-mono text-ink tabular-nums">{sum('wins')}/{sum('losses')}</span></span
	>
	{#if balance !== null}
		<span class="text-muted"
			>Balance <span class="font-mono text-ink tabular-nums">{formatAmount(balance)}</span>
			{currency}</span
		>
	{/if}
	<span class="text-muted"
		>ROI <span class="font-mono text-ink tabular-nums">{roi.toFixed(2)}%</span></span
	>
	<span class="text-muted"
		>bps <span class="font-mono text-ink tabular-nums">{sum('bps_1s')}</span></span
	>
	{#if p50 !== null}
		<span class="text-muted"
			>API <span class="font-mono text-ink tabular-nums"
				>{Math.round(p50)}/{Math.round(p99 ?? p50)} ms</span
			></span
		>
	{/if}
	<ul class="flex gap-2" aria-label="Lanes">
		{#each lanes as lane (lane.n)}
			{@const status = lane.status ?? 'stale'}
			<li
				class="rounded-sm border px-2 py-0.5 text-xs {ATTENTION.has(status)
					? 'border-warn text-warn'
					: 'border-line text-muted'}"
			>
				Lane {lane.n}: {STATUS_WORDS[status]}
			</li>
		{/each}
	</ul>
</div>

<style>
	.flash-up {
		animation: flash-up var(--motion-flash) var(--motion-ease);
	}
	.flash-down {
		animation: flash-down var(--motion-flash) var(--motion-ease);
	}
	@keyframes flash-up {
		from {
			background: color-mix(in srgb, var(--color-profit) 30%, transparent);
		}
	}
	@keyframes flash-down {
		from {
			background: color-mix(in srgb, var(--color-loss) 30%, transparent);
		}
	}
</style>
