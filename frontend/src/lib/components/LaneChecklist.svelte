<script lang="ts">
	let {
		legend,
		choices,
		value,
		label,
		onchange
	}: {
		legend: string;
		choices: number[];
		value: number[];
		label: (lane: number) => string;
		onchange: (lanes: number[]) => void;
	} = $props();

	function flip(lane: number, on: boolean) {
		const next = on ? [...value, lane] : value.filter((n) => n !== lane);
		onchange([...new Set(next)].sort((a, b) => a - b));
	}
</script>

<fieldset class="flex flex-col gap-1 text-sm">
	<legend class="font-medium">{legend}</legend>
	{#if choices.length}
		<div class="flex max-h-48 flex-col gap-1 overflow-y-auto">
			{#each choices as lane (lane)}
				<label class="flex min-h-9 items-center gap-2">
					<input
						type="checkbox"
						class="size-4"
						checked={value.includes(lane)}
						onchange={(e) => flip(lane, e.currentTarget.checked)}
					/>
					{label(lane)}
				</label>
			{/each}
		</div>
		<span class="text-xs text-muted">None ticked means every lane.</span>
	{:else}
		<span class="text-xs text-muted">No lanes reported by the bot yet.</span>
	{/if}
</fieldset>
