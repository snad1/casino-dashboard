<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import { page } from '$app/state';
	import ControlDialogs from '#components/ControlDialogs';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import { commentsTopicFor, laneTopicFor, systemTopicFor, topicFor } from '#lib/live/protocol';
	import { laneScope } from '#lib/permissions';
	import { session } from '#lib/session.svelte';
	import Guard from '#ui/Guard';

	let { children }: { children: Snippet } = $props();

	let current: string | null = null;
	$effect(() => {
		const service = page.params.service;
		if (!service || service === current) return;
		if (current) liveFor(current).stop();
		current = service;
		const lanes = laneScope(session.me, 'view', service);
		if (lanes === null) {
			liveFor(service).start(topicFor(service));
			liveFor(service).start(systemTopicFor(service));
			liveFor(service).start(commentsTopicFor(service));
			void control.load(service);
		} else {
			for (const lane of lanes) liveFor(service).start(laneTopicFor(service, lane));
		}
	});
	onDestroy(() => {
		if (current) liveFor(current).stop();
	});
</script>

<Guard perm="view" service={page.params.service ?? null} lanes>
	{@render children()}
	<ControlDialogs />
</Guard>
