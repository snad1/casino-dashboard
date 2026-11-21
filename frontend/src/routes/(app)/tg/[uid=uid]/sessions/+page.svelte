<script lang="ts">
	import { page } from '$app/state';
	import SessionsList from '#components/SessionsList';
	import { tgSource } from '#lib/history-source';
	import { liveFor } from '#lib/live/index';
	import { sessionsCsvUrl, TG_SERVICE } from '#lib/tg';

	const uid = $derived(Number(page.params.uid));
</script>

<svelte:head><title>Sessions · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-3xl flex-col gap-4">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h1 class="text-xl font-semibold">Sessions</h1>
		<!-- eslint-disable svelte/no-navigation-without-resolve -- an api download, not an app route -->
		<a
			href={sessionsCsvUrl(uid)}
			download
			class="inline-flex min-h-11 items-center rounded-md border border-line-strong px-3 text-sm text-accent"
			>Download CSV</a
		>
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
	</div>
	<div class="h-[36rem] rounded-lg border border-line bg-surface p-2">
		{#key uid}
			<SessionsList live={liveFor(TG_SERVICE)} service={TG_SERVICE} source={tgSource(uid)} />
		{/key}
	</div>
</div>
