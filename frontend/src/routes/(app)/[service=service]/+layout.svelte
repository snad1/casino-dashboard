<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import { page } from '$app/state';
	import ControlDialogs from '#components/ControlDialogs';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import { commentsTopicFor, systemTopicFor, topicFor } from '#lib/live/protocol';
	import Guard from '#ui/Guard';

	let { children }: { children: Snippet } = $props();

	let current: string | null = null;
	$effect(() => {
		const service = page.params.service;
		if (!service || service === current) return;
		if (current) liveFor(current).stop();
		current = service;
		liveFor(service).start(topicFor(service));
		liveFor(service).start(systemTopicFor(service));
		liveFor(service).start(commentsTopicFor(service));
		void control.load(service);
	});
	onDestroy(() => {
		if (current) liveFor(current).stop();
	});
</script>

<Guard perm="view" service={page.params.service ?? null}>
	{@render children()}
	<ControlDialogs />
</Guard>
