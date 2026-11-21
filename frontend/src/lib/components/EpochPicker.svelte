<script lang="ts">
	import { unwrap } from '#lib/api/client';
	import { epochLabel, type Epoch } from '#lib/epochs';
	import { api } from '#lib/session.svelte';

	let {
		service,
		value,
		onchange
	}: { service: string; value: number | null; onchange: (epoch: number | null) => void } = $props();

	let epochs: Epoch[] = $state.raw([]);

	/** The databases a reset archived, newest first; the current one is the default. */
	$effect(() => {
		const name = service;
		unwrap(api.GET('/{service}/epochs', { params: { path: { service: name } } }))
			.then((out) => {
				if (name === service) epochs = out.items.filter((e) => !e.current);
			})
			.catch(() => (epochs = []));
	});
</script>

{#if epochs.length || value !== null}
	<label class="flex flex-col gap-1 text-sm font-medium">
		Previous resets
		<select
			class="min-h-11 rounded-md border border-line-strong bg-surface px-3"
			value={value === null ? '' : String(value)}
			onchange={(e) => {
				const picked = e.currentTarget.value;
				onchange(picked ? Number(picked) : null);
			}}
		>
			<option value="">Since last reset</option>
			{#each epochs as epoch (epoch.epoch_id)}
				<option value={String(epoch.epoch_id)}>{epochLabel(epoch)}</option>
			{/each}
		</select>
	</label>
{/if}
