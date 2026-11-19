<script lang="ts">
	import type { Snippet } from 'svelte';
	import { can, type Perm } from '#lib/permissions';
	import { session } from '#lib/session.svelte';

	let {
		perm,
		service = null,
		children
	}: { perm: Perm; service?: string | null; children: Snippet } = $props();
</script>

{#if can(session.me, perm, service)}
	{@render children()}
{:else}
	<section class="mx-auto max-w-lg rounded-lg border border-line bg-surface p-6">
		<h1 class="text-lg font-semibold">You don't have access to this page.</h1>
		<p class="mt-2 text-sm text-muted">Ask an admin for a role that includes it.</p>
	</section>
{/if}
