<script lang="ts">
	import { formatAmount, formatProfit } from '#lib/format';
	import type { TapeRow } from '#lib/live/protocol';
	import type { LiveStore } from '#lib/live/store.svelte';
	import VirtualList from '#ui/VirtualList';

	let { live }: { live: LiveStore } = $props();

	const ROW = 28;
	let lane: number | 'all' = $state('all');
	let paused = $state(false);
	let frozen: TapeRow[] = $state.raw([]);

	const keyOf = (row: TapeRow) => `${row.lane}:${row.seq}:${row.ts_ms}`;
	const laneNumbers = $derived(
		Object.keys(live.lanes)
			.map(Number)
			.sort((a, b) => a - b)
	);
	const current = $derived(lane === 'all' ? live.tape : live.tape.filter((r) => r.lane === lane));
	const shown = $derived(paused ? frozen : current);
	const waiting = $derived.by(() => {
		if (!paused) return 0;
		const top = frozen[0];
		if (!top) return current.length;
		const index = current.findIndex((r) => keyOf(r) === keyOf(top));
		return index === -1 ? current.length : index;
	});

	function pause() {
		if (paused) return;
		frozen = current;
		paused = true;
	}

	function resume() {
		paused = false;
	}

	function leave(event: FocusEvent) {
		const to = event.relatedTarget as Node | null;
		if (!to || !(event.currentTarget as HTMLElement).contains(to)) resume();
	}

	const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="flex h-full flex-col gap-2"
	onmouseenter={pause}
	onmouseleave={resume}
	onfocusin={pause}
	onfocusout={leave}
>
	<div class="flex items-center justify-between gap-2 text-sm">
		<label class="flex items-center gap-2 text-muted">
			Lane
			<select
				value={String(lane)}
				onchange={(e) => {
					const value = e.currentTarget.value;
					lane = value === 'all' ? 'all' : Number(value);
					paused = false;
				}}
				class="min-h-9 rounded-md border border-line-strong bg-surface px-2 text-ink"
			>
				<option value="all">All lanes</option>
				{#each laneNumbers as n (n)}<option value={String(n)}>Lane {n}</option>{/each}
			</select>
		</label>
		{#if paused}
			<button
				type="button"
				onclick={resume}
				class="min-h-9 rounded-md bg-raised px-3 text-xs text-ink focus-visible:outline-2 focus-visible:outline-focus"
				>{waiting} new</button
			>
		{/if}
	</div>
	<div class="min-h-0 flex-1 overflow-x-auto">
		<div class="h-full min-w-76">
			<VirtualList items={shown} key={keyOf} rowHeight={ROW} label="Bet tape">
				{#snippet header()}
					<div role="rowgroup">
						<div
							role="row"
							class="grid grid-cols-[4.5rem_2rem_4.5rem_1fr_3.5rem_1fr] gap-2 border-b border-line px-1 py-1 text-xs text-muted"
						>
							<span role="columnheader">Time</span>
							<span role="columnheader">Lane</span>
							<span role="columnheader">Result</span>
							<span role="columnheader" class="text-right">Amount</span>
							<span role="columnheader" class="text-right">Mult.</span>
							<span role="columnheader" class="text-right">Profit</span>
						</div>
					</div>
				{/snippet}
				{#snippet row(bet: TapeRow)}
					{@const win = bet.state === 'win'}
					<div
						role="row"
						tabindex="-1"
						class="grid h-full grid-cols-[4.5rem_2rem_4.5rem_1fr_3.5rem_1fr] items-center gap-2 px-1 font-mono text-xs tabular-nums"
						title="Roll {bet.result}, balance {formatAmount(bet.balance)}"
					>
						<span role="cell" class="text-muted">{clock(bet.ts_ms)}</span>
						<span role="cell" class="text-muted">{bet.lane}</span>
						<span role="cell" class={win ? 'text-profit' : 'text-loss'}
							><span aria-hidden="true">{win ? '▲' : '▼'}</span> {win ? 'Win' : 'Loss'}</span
						>
						<span role="cell" class="text-right">{formatAmount(bet.amount)}</span>
						<span role="cell" class="text-right">{bet.multiplier.toFixed(2)}x</span>
						<span role="cell" class="text-right {win ? 'text-profit' : 'text-loss'}"
							>{formatProfit(bet.profit)}</span
						>
					</div>
				{/snippet}
			</VirtualList>
		</div>
	</div>
</div>
