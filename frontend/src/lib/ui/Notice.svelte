<script lang="ts">
	import type { Snippet } from 'svelte';

	let { tone = 'info', children }: { tone?: 'info' | 'success' | 'error'; children: Snippet } =
		$props();

	const looks = {
		info: 'border-info text-ink',
		success: 'border-profit text-ink',
		error: 'border-loss text-ink'
	};
	const labels = { info: 'Note', success: 'Done', error: 'Problem' };
</script>

<div
	role={tone === 'error' ? 'alert' : 'status'}
	class="rounded-md border-l-4 bg-raised px-4 py-3 text-sm {looks[tone]}"
>
	<span class="sr-only">{labels[tone]}:</span>
	{@render children()}
</div>
