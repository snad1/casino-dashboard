<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	type Props = Omit<HTMLInputAttributes, 'value'> & {
		label: string;
		value: string;
		hint?: string;
		error?: string | null;
	};

	let { label, value = $bindable(''), hint, error = null, ...rest }: Props = $props();
	const id = $props.id();
</script>

<div class="flex flex-col gap-1">
	<label for={id} class="text-sm font-medium text-ink">{label}</label>
	<input
		{id}
		bind:value
		aria-invalid={error ? 'true' : undefined}
		aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
		class="min-h-11 rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
		{...rest}
	/>
	{#if error}
		<p id="{id}-error" class="text-sm text-loss">{error}</p>
	{:else if hint}
		<p id="{id}-hint" class="text-sm text-muted">{hint}</p>
	{/if}
</div>
