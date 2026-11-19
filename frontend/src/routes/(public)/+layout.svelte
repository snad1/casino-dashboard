<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children }: { children: Snippet } = $props();
</script>

<main class="flex min-h-dvh items-center justify-center bg-canvas p-4 text-ink">
	<div class="w-full max-w-sm rounded-lg border border-line bg-surface p-6">
		{@render children()}
	</div>
</main>
