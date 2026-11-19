<script lang="ts">
	import { page } from '$app/state';
	import BetTape from '#components/BetTape';
	import Chart from '#components/Chart';
	import SessionsList from '#components/SessionsList';
	import Streaks from '#components/Streaks';
	import { liveFor } from '#lib/live/index';

	const panels = [
		{ id: 'sessions', title: 'Sessions', area: 'lg:col-span-3 lg:row-span-3' },
		{ id: 'chart', title: 'Chart', area: 'lg:col-span-6' },
		{ id: 'tape', title: 'Bet tape', area: 'lg:col-span-3 lg:row-span-3' },
		{ id: 'lanes', title: 'Lanes', area: 'lg:col-span-6' },
		{ id: 'streaks', title: 'Streaks', area: 'lg:col-span-6' },
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
			{#if panel.id === 'chart' && page.params.service}
				<div class="mt-2 h-80">
					<Chart live={liveFor(page.params.service)} service={page.params.service} />
				</div>
			{:else if panel.id === 'sessions' && page.params.service}
				<div class="mt-2 h-[36rem]">
					<SessionsList live={liveFor(page.params.service)} service={page.params.service} />
				</div>
			{:else if panel.id === 'tape' && page.params.service}
				<div class="mt-2 h-[36rem]">
					<BetTape live={liveFor(page.params.service)} />
				</div>
			{:else if panel.id === 'streaks' && page.params.service}
				<div class="mt-2"><Streaks live={liveFor(page.params.service)} /></div>
			{:else}
				<p class="mt-2 text-sm text-muted">Waiting for live data.</p>
			{/if}
		</section>
	{/each}
</div>
