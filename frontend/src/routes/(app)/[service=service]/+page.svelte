<script lang="ts">
	import { page } from '$app/state';

	const panels = [
		{ id: 'sessions', title: 'Sessions', area: 'lg:col-span-3 lg:row-span-2' },
		{ id: 'chart', title: 'Chart', area: 'lg:col-span-6' },
		{ id: 'tape', title: 'Bet tape', area: 'lg:col-span-3 lg:row-span-2' },
		{ id: 'lanes', title: 'Lanes', area: 'lg:col-span-6' },
		{ id: 'system', title: 'System', area: 'lg:col-span-12' }
	];
</script>

<svelte:head><title>{page.params.service} · Casino dashboard</title></svelte:head>

<h1 class="sr-only">{page.params.service} live dashboard</h1>
<div class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-12">
	{#each panels as panel (panel.id)}
		<section
			id={panel.id}
			aria-labelledby="{panel.id}-title"
			class="min-h-40 rounded-lg border border-line bg-surface p-4 {panel.area}"
		>
			<h2 id="{panel.id}-title" class="text-sm font-semibold text-muted">{panel.title}</h2>
			<p class="mt-2 text-sm text-muted">Waiting for live data.</p>
		</section>
	{/each}
</div>
