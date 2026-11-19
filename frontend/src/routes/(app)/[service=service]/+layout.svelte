<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import Guard from '#ui/Guard';

	let { children }: { children: Snippet } = $props();
</script>

<Guard perm="view" service={page.params.service ?? null}>
	{@render children()}
</Guard>
