<script lang="ts">
	import type { Snippet } from 'svelte';
	import { can, laneScope, type Perm } from '#lib/permissions';
	import { session } from '#lib/session.svelte';

	/** With `lanes`, a user whose lane roles on `service` grant `perm` is let in too. */
	let {
		perm,
		service = null,
		lanes = false,
		children
	}: { perm: Perm; service?: string | null; lanes?: boolean; children: Snippet } = $props();

	const allowed = $derived(
		can(session.me, perm, service) ||
			(lanes && service !== null && (laneScope(session.me, perm, service)?.length ?? 0) > 0)
	);
</script>

{#if allowed}
	{@render children()}
{:else}
	<section class="mx-auto max-w-lg rounded-lg border border-line bg-surface p-6">
		<h1 class="text-lg font-semibold">You don't have access to this page.</h1>
		<p class="mt-2 text-sm text-muted">Ask an admin for a role that includes it.</p>
	</section>
{/if}
