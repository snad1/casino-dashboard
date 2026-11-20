<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api } from '#lib/session.svelte';
	import { formatValue } from '#lib/status';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';

	type Status = components['schemas']['StatusOut'];
	const EVERY_MS = 5000;

	let current = $state<Status | null>(null);
	let error: string | null = $state(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		if (document.visibilityState === 'hidden') return;
		try {
			current = await unwrap(api.GET('/status'));
			error = null;
		} catch (e) {
			error = e instanceof ApiError ? e.detail : String(e);
		}
	}

	onMount(() => {
		void load();
		timer = setInterval(() => void load(), EVERY_MS);
	});
	onDestroy(() => clearInterval(timer));

	const warnings = $derived(current?.checks.filter((c) => !c.ok).length ?? 0);
</script>

<svelte:head><title>Status · Casino dashboard</title></svelte:head>

<Guard perm="audit.read">
	<div class="mx-auto flex max-w-3xl flex-col gap-4">
		<h1 class="text-xl font-semibold">Status</h1>
		{#if error}<Notice tone="error">{error}</Notice>{/if}
		{#if current === null}
			<p class="text-sm text-muted" role="status">Loading status...</p>
		{:else}
			<p class="text-sm text-muted" role="status">
				{warnings === 0 ? 'Everything is OK.' : `${warnings} need attention.`}
				Updated {new Date(current.at).toLocaleTimeString()}.
			</p>
			<div class="overflow-x-auto">
				<table class="w-full text-left text-sm">
					<thead class="text-muted">
						<tr
							><th class="p-2">Check</th><th class="p-2">State</th><th class="p-2">Value</th><th
								class="p-2">What it means</th
							></tr
						>
					</thead>
					<tbody>
						{#each current.checks as check (check.name)}
							<tr class="border-t border-line">
								<td class="p-2 font-medium">{check.name}</td>
								<td class="p-2">
									<span
										class="rounded px-2 py-0.5 text-xs font-semibold {check.ok
											? 'bg-raised text-profit'
											: 'bg-raised text-warn'}">{check.ok ? 'OK' : 'Warning'}</span
									>
								</td>
								<td class="p-2 font-mono tabular-nums">{formatValue(check.value, check.unit)}</td>
								<td class="p-2 text-muted">{check.detail}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</Guard>
