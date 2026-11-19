<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import { page } from '$app/state';
	import { liveFor } from '#lib/live/index';
	import { topicFor } from '#lib/live/protocol';
	import Guard from '#ui/Guard';

	let { children }: { children: Snippet } = $props();

	let current: string | null = null;
	$effect(() => {
		const service = page.params.service;
		if (!service || service === current) return;
		if (current) liveFor(current).stop();
		current = service;
		liveFor(service).start(topicFor(service));
	});
	onDestroy(() => {
		if (current) liveFor(current).stop();
	});
</script>

<Guard perm="view" service={page.params.service ?? null}>
	{@render children()}
</Guard>
