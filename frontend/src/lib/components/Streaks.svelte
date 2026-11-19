<script lang="ts">
	import type { LiveStore } from '#lib/live/store.svelte';
	import { streakHistogram } from '#lib/tape';

	let { live }: { live: LiveStore } = $props();

	const MAX_BARS = 10;
	const lanes = $derived(
		Object.entries(live.lanes)
			.map(([n, lane]) => ({ n: Number(n), ...lane }))
			.sort((a, b) => a.n - b.n)
	);
	const histogram = $derived(streakHistogram(live.tape, 'all').slice(0, MAX_BARS));
	const top = $derived(Math.max(1, ...histogram.flatMap((b) => [b.wins, b.losses])));
	const streakText = (n = 0) => (n > 0 ? `W${n}` : n < 0 ? `L${-n}` : '0');
</script>

<div class="flex flex-col gap-4 text-sm">
	<table class="w-full text-left">
		<thead class="text-xs text-muted">
			<tr><th class="py-1">Lane</th><th>Current</th><th>Max win</th><th>Max loss</th></tr>
		</thead>
		<tbody class="font-mono tabular-nums">
			{#each lanes as lane (lane.n)}
				<tr class="border-t border-line">
					<td class="py-1">{lane.n}</td>
					<td class={(lane.current_streak ?? 0) < 0 ? 'text-loss' : 'text-profit'}
						>{streakText(lane.current_streak)}</td
					>
					<td>{lane.max_win_streak ?? 0}</td>
					<td>{lane.max_loss_streak ?? 0}</td>
				</tr>
			{/each}
		</tbody>
	</table>
	<figure>
		<figcaption class="mb-2 text-xs text-muted">
			Streak lengths over the last {live.tape.length} bets
		</figcaption>
		<ul class="flex flex-col gap-1">
			{#each histogram as bucket (bucket.length)}
				<li class="grid grid-cols-[2rem_1fr_1fr] items-center gap-2 font-mono text-xs tabular-nums">
					<span class="text-muted">{bucket.length}</span>
					<span class="flex items-center gap-1">
						<span class="h-2 rounded-sm bg-profit" style:width="{(bucket.wins / top) * 100}%"
						></span>
						<span>{bucket.wins} win runs</span>
					</span>
					<span class="flex items-center gap-1">
						<span class="h-2 rounded-sm bg-loss" style:width="{(bucket.losses / top) * 100}%"
						></span>
						<span>{bucket.losses} loss runs</span>
					</span>
				</li>
			{/each}
		</ul>
	</figure>
</div>
