<script lang="ts">
	import type { JobLine } from '#lib/live/protocol';

	let {
		lines,
		done,
		label = 'Output lines',
		class: extra = ''
	}: { lines: JobLine[]; done: boolean; label?: string; class?: string } = $props();
</script>

<ol class="overflow-auto font-mono text-xs whitespace-pre-wrap {extra}" aria-label={label}>
	{#each lines as line (line.seq)}
		<li class={line.stream === 'stderr' ? 'text-loss' : 'text-ink'}>{line.line}</li>
	{:else}
		<li class="text-muted">{done ? 'No output.' : 'Waiting for output...'}</li>
	{/each}
</ol>
