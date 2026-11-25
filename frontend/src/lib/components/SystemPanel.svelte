<script lang="ts">
	import { onMount } from 'svelte';
	import { unwrap } from '#lib/api/client';
	import type { LiveStore } from '#lib/live/store.svelte';
	import { api } from '#lib/session.svelte';
	import {
		droppedLastMinute,
		formatBytes,
		formatPercent,
		ingestWarnings,
		publisherWarnings,
		type ReconcileRun
	} from '#lib/system';

	let { live, service }: { live: LiveStore; service: string } = $props();

	const INGEST_EVERY_MS = 60_000;
	let run = $state<ReconcileRun | null>(null);
	let ingestError = $state(false);

	const system = $derived(live.system);
	const host = $derived(system?.host ?? null);
	const dropped = $derived(droppedLastMinute(live.dropSamples));
	const publishers = $derived(
		Object.entries(system?.publisher ?? {})
			.map(([lane, counters]) => ({ lane: Number(lane), ...counters }))
			.sort((a, b) => a.lane - b.lane)
	);
	const warnings = $derived([
		...publisherWarnings(live.dropSamples, live.lanes),
		...ingestWarnings(run)
	]);
	const memUsed = $derived(
		host && typeof host.mem_total === 'number' && typeof host.mem_available === 'number'
			? host.mem_total - host.mem_available
			: null
	);

	async function loadIngest() {
		try {
			run = (await unwrap(api.GET('/{service}/ingest', { params: { path: { service } } })))
				.last_run;
			ingestError = false;
		} catch {
			ingestError = true;
		}
	}

	onMount(() => {
		void loadIngest();
		const timer = setInterval(loadIngest, INGEST_EVERY_MS);
		return () => clearInterval(timer);
	});
</script>

<div class="flex flex-col gap-4 text-sm">
	{#if warnings.length}
		<ul
			role="alert"
			class="flex flex-col gap-1 rounded-md border-l-4 border-warn bg-raised px-3 py-2"
		>
			{#each warnings as warning (warning)}<li>{warning}</li>{/each}
		</ul>
	{/if}

	<div class="grid gap-4 lg:grid-cols-3">
		<section aria-labelledby="host-title">
			<h3 id="host-title" class="mb-1 text-xs font-semibold text-muted">Host</h3>
			{#if system?.agent === 'unavailable'}
				<p class="text-warn">Host metrics unavailable</p>
			{:else if !host}
				<p class="text-muted">Waiting for metrics</p>
			{:else}
				<dl class="grid grid-cols-2 gap-x-3 gap-y-1 font-mono tabular-nums">
					<dt class="text-muted">CPU</dt>
					<dd>{formatPercent(host.cpu_pct)}</dd>
					<dt class="text-muted">Load</dt>
					<dd>{host.load ? host.load.map((v) => v.toFixed(2)).join(' ') : 'n/a'}</dd>
					<dt class="text-muted">Memory</dt>
					<dd>{formatBytes(memUsed)} of {formatBytes(host.mem_total)}</dd>
					<dt class="text-muted">Disk free</dt>
					<dd>{formatBytes(host.disk_free)} of {formatBytes(host.disk_total)}</dd>
				</dl>
			{/if}
		</section>

		<section aria-labelledby="units-title">
			<h3 id="units-title" class="mb-1 text-xs font-semibold text-muted">Processes</h3>
			<table class="w-full text-left font-mono text-xs tabular-nums">
				<thead class="text-muted"><tr><th>Name</th><th>CPU</th><th>Memory</th></tr></thead>
				<tbody>
					{#each system?.units ?? [] as unit (unit.unit)}
						<tr
							><td>{unit.unit}</td><td>{formatPercent(unit.cpu_pct)}</td><td
								>{formatBytes(unit.rss_bytes)}</td
							></tr
						>
					{/each}
					{#each system?.containers ?? [] as container (container.service)}
						<tr
							><td>{container.service}</td><td>{formatPercent(container.cpu_pct)}</td><td
								>{formatBytes(container.memory_bytes)}</td
							></tr
						>
					{/each}
				</tbody>
			</table>
		</section>

		<section aria-labelledby="pipeline-title">
			<h3 id="pipeline-title" class="mb-1 text-xs font-semibold text-muted">Events and history</h3>
			<table class="mb-2 w-full text-left font-mono text-xs tabular-nums">
				<thead class="text-muted"
					><tr><th>Lane</th><th>Sent</th><th>Dropped</th><th>Link</th></tr></thead
				>
				<tbody>
					{#each publishers as p (p.lane)}
						<tr>
							<td>{p.lane}</td>
							<td>{p.sent}</td>
							<td class={dropped[p.lane] ? 'text-warn' : ''}>{p.dropped}</td>
							<td>{!p.enabled ? 'Off' : p.connected ? 'Connected' : 'Disconnected'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if ingestError}
				<p class="text-warn">History status unavailable</p>
			{:else if run}
				<p>
					History lag {run.lag_rows} bets ({Math.round(run.lag_seconds)} s); last check
					{run.mismatched === 0 ? 'matched' : `found ${run.mismatched} differences`}
					at {new Date(run.finished_at).toLocaleTimeString()}.
				</p>
			{:else}
				<p class="text-muted">No history check yet</p>
			{/if}
		</section>
	</div>
</div>
