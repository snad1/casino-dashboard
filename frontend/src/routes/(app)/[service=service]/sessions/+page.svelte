<script lang="ts">
	import { page } from '$app/state';
	import SessionsList from '#components/SessionsList';
	import type { SessionsQuery } from '#lib/history-source';
	import { liveFor } from '#lib/live/index';
	import { laneScope } from '#lib/permissions';
	import { session } from '#lib/session.svelte';
	import Field from '#ui/Field';

	const service = $derived(page.params.service ?? '');
	const live = $derived(liveFor(service));

	let lane = $state('');
	let status = $state('');
	let from = $state('');
	let to = $state('');
	let total = $state<number | null>(null);

	const ownLanes = $derived(laneScope(session.me, 'view', service));
	const lanes = $derived(
		ownLanes ??
			Object.keys(live.lanes)
				.map(Number)
				.filter((n) => Number.isInteger(n))
				.sort((a, b) => a - b)
	);

	$effect(() => {
		if (ownLanes?.length && !ownLanes.includes(Number(lane))) lane = String(ownLanes[0]);
	});

	const startOfDay = (day: string, plusDays = 0) => {
		const [year = 0, month = 1, date = 1] = day.split('-').map(Number);
		return new Date(year, month - 1, date + plusDays).toISOString();
	};

	const filters = $derived<Omit<SessionsQuery, 'limit' | 'cursor'>>({
		...(lane ? { lane: Number(lane) } : {}),
		...(status === 'running' || status === 'ended' ? { status } : {}),
		...(from ? { since: startOfDay(from) } : {}),
		...(to ? { until: startOfDay(to, 1) } : {})
	});
	const filterKey = $derived(JSON.stringify(filters));

	$effect(() => {
		void filterKey;
		total = null;
	});

	const selectClass =
		'min-h-11 rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';
</script>

<svelte:head><title>Sessions · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h1 class="text-xl font-semibold">Sessions</h1>
		<p class="text-sm text-muted" aria-live="polite">
			{total === null
				? 'Counting sessions...'
				: `${total.toLocaleString()} ${total === 1 ? 'session' : 'sessions'}`}
		</p>
	</div>
	<form class="grid grid-cols-2 gap-3 sm:grid-cols-4" onsubmit={(e) => e.preventDefault()}>
		<label class="flex flex-col gap-1 text-sm font-medium text-ink">
			Lane
			<select bind:value={lane} class={selectClass}>
				{#if ownLanes === null}<option value="">All lanes</option>{/if}
				{#each lanes as n (n)}
					<option value={String(n)}>Lane {n}</option>
				{/each}
			</select>
		</label>
		<label class="flex flex-col gap-1 text-sm font-medium text-ink">
			Status
			<select bind:value={status} class={selectClass}>
				<option value="">Any</option>
				<option value="running">Running</option>
				<option value="ended">Ended</option>
			</select>
		</label>
		<Field label="From" type="date" bind:value={from} />
		<Field label="To" type="date" bind:value={to} />
	</form>
	<div class="h-[36rem] rounded-lg border border-line bg-surface p-2">
		{#key `${service}|${filterKey}`}
			<SessionsList
				{live}
				{service}
				{filters}
				empty="No sessions match these filters."
				ontotal={(n) => (total = n)}
			/>
		{/key}
	</div>
</div>
