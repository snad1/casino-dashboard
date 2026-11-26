<script lang="ts">
	import { onMount } from 'svelte';
	import { accounts, keysOf, lanesOf, type Account } from '#lib/accounts';
	import { formatAmount, formatProfit } from '#lib/format';
	import { keyFilter } from '#lib/key-filter.svelte';
	import { services } from '#lib/services.svelte';
	import { LANE_STATUS_WORDS, laneLabel } from '#lib/lanes';
	import type { LaneStatus } from '#lib/live/protocol';
	import type { LiveStore } from '#lib/live/store.svelte';

	let { live, service }: { live: LiveStore; service: string } = $props();

	const ATTENTION = new Set<LaneStatus>(['stale', 'down', 'events_off']);

	const selected = $derived(keyFilter.get(service));
	const keys = $derived(keysOf(live.lanes));
	const shown = $derived(lanesOf(live.lanes, selected));
	const lanes = $derived(
		Object.entries(shown)
			.map(([n, lane]) => ({ n: Number(n), ...lane }))
			.sort((a, b) => a.n - b.n)
	);
	const perAccount = $derived(accounts(shown));
	const split = $derived(selected === null && keys.length > 1);

	const sum = (key: 'profit' | 'total_bets' | 'wins' | 'losses' | 'wagered' | 'bps_1s') =>
		lanes.reduce((total, lane) => total + (lane[key] ?? 0), 0);
	const worst = (key: 'api_ms_p50' | 'api_ms_p99') => {
		const values = lanes.map((lane) => lane[key]).filter((v): v is number => typeof v === 'number');
		return values.length ? Math.max(...values) : null;
	};

	const profit = $derived(sum('profit'));
	const wagered = $derived(sum('wagered'));
	const roi = $derived(wagered > 0 ? (profit / wagered) * 100 : 0);
	const p50 = $derived(worst('api_ms_p50'));
	const p99 = $derived(worst('api_ms_p99'));
	const attention = $derived(lanes.filter((lane) => ATTENTION.has(lane.status ?? 'stale')).length);
	const betting = $derived(
		lanes.filter((lane) => lane.status === 'running' || lane.status === 'paused').length
	);

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

	const label = (key: string) => keyFilter.label(service, key);
	const balanceText = (account: Account) =>
		account.balance === null ? '-' : `${formatAmount(account.balance)} ${account.currency}`;

	onMount(() => void keyFilter.loadLabels(service));
</script>

<div class="flex min-w-0 flex-1 items-center gap-3 text-sm">
	{#if services.list.length <= 1}
		<span class="font-semibold">{services.info(service)?.label ?? service}</span>
	{/if}
	<span
		class="rounded-sm px-2 py-0.5 text-xs {link === 'Live'
			? 'bg-raised text-profit'
			: 'bg-raised text-warn'}"
		role="status"
		aria-live="polite">{link}</span
	>
	{#if keys.length > 1 || selected !== null}
		<label class="flex items-center gap-1 text-muted">
			<span class="sr-only sm:not-sr-only">Key</span>
			<select
				class="min-h-9 rounded-md border border-line-strong bg-surface px-2 text-ink"
				value={selected ?? ''}
				onchange={(event) => keyFilter.set(service, event.currentTarget.value || null)}
			>
				<option value="">All keys</option>
				{#each [...new Set([...keys, ...(selected ? [selected] : [])])] as key (key)}
					<option value={key}>{label(key)}</option>
				{/each}
			</select>
		</label>
	{/if}

	<!-- A scrollable region must be reachable by keyboard so its overflow can be scrolled. -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		role="group"
		aria-label="Live totals"
		tabindex="0"
		class="flex min-w-0 flex-1 items-center gap-4 overflow-x-auto focus-visible:outline-2 focus-visible:outline-focus"
	>
		{#if split}
			{#each perAccount as account (`${account.key}|${account.currency}`)}
				<span
					class="flex shrink-0 items-center gap-2 rounded-md border border-line px-2 py-0.5"
					aria-label="{label(account.key)}: balance {balanceText(account)}, profit {formatProfit(
						account.profit
					)}, {account.running} of {account.lanes.length} lanes betting"
				>
					<span class="font-medium">{label(account.key)}</span>
					<span class="font-mono tabular-nums">{balanceText(account)}</span>
					<span class="font-mono tabular-nums {account.profit < 0 ? 'text-loss' : 'text-profit'}"
						>{formatProfit(account.profit)}</span
					>
					<span class="text-xs text-muted">{account.running}/{account.lanes.length} lanes</span>
				</span>
			{/each}
		{:else}
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
				>W/L <span class="font-mono text-ink tabular-nums">{sum('wins')}/{sum('losses')}</span
				></span
			>
			{#each perAccount as account (`${account.key}|${account.currency}`)}
				{#if account.balance !== null}
					<span class="text-muted"
						>Balance <span class="font-mono text-ink tabular-nums"
							>{formatAmount(account.balance)}</span
						>
						{account.currency}</span
					>
				{/if}
			{/each}
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
		{/if}
	</div>

	{#if lanes.length}
		<details class="relative shrink-0">
			<summary
				class="flex min-h-9 cursor-pointer list-none items-center rounded-md border px-2 text-xs {attention
					? 'border-warn text-warn'
					: 'border-line text-muted'}"
			>
				Lanes: {betting} betting{attention ? `, ${attention} need a look` : ''}
			</summary>
			<ul
				aria-label="Lanes"
				class="absolute right-0 top-full z-20 mt-2 flex max-h-80 w-64 flex-col gap-1 overflow-y-auto rounded-md border border-line bg-surface p-2 shadow-lg"
			>
				{#each lanes as lane (lane.n)}
					{@const status = lane.status ?? 'stale'}
					<li
						class="flex justify-between gap-2 rounded-sm px-2 py-1 text-xs {ATTENTION.has(status)
							? 'text-warn'
							: 'text-muted'}"
					>
						<span class="truncate text-ink">{laneLabel(lane.n, live.lanes)}</span>
						<span>{LANE_STATUS_WORDS[status]}</span>
					</li>
				{/each}
			</ul>
		</details>
	{/if}
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
