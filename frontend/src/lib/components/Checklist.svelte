<script lang="ts" generics="T extends string | number">
	let {
		legend,
		choices,
		value,
		label,
		hint = 'None ticked means every lane.',
		empty,
		onchange
	}: {
		legend: string;
		choices: T[];
		value: T[];
		label: (choice: T) => string;
		hint?: string;
		empty: string;
		onchange: (picked: T[]) => void;
	} = $props();

	function flip(choice: T, on: boolean) {
		const next = on ? [...value, choice] : value.filter((c) => c !== choice);
		onchange(choices.filter((c) => next.includes(c)));
	}
</script>

<fieldset class="flex flex-col gap-1 text-sm">
	<legend class="font-medium">{legend}</legend>
	{#if choices.length}
		<div class="flex max-h-48 flex-col gap-1 overflow-y-auto">
			{#each choices as choice (choice)}
				<label class="flex min-h-9 items-center gap-2">
					<input
						type="checkbox"
						class="size-4"
						checked={value.includes(choice)}
						onchange={(e) => flip(choice, e.currentTarget.checked)}
					/>
					{label(choice)}
				</label>
			{/each}
		</div>
		<span class="text-xs text-muted">{hint}</span>
	{:else}
		<span class="text-xs text-muted">{empty}</span>
	{/if}
</fieldset>
