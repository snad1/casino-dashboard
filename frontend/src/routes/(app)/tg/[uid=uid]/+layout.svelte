<script lang="ts">
	import { onDestroy, type Snippet } from 'svelte';
	import { page } from '$app/state';
	import { liveFor } from '#lib/live/index';
	import { userTopicFor } from '#lib/live/protocol';
	import { session } from '#lib/session.svelte';
	import { canSeeTgUser, FORBIDDEN, TG_SERVICE } from '#lib/tg';
	import { tgControl } from '#lib/tg-control.svelte';
	import ConfirmDialog from '#ui/ConfirmDialog';

	let { children }: { children: Snippet } = $props();

	const uid = $derived(Number(page.params.uid));
	const allowed = $derived(canSeeTgUser(session.me, uid));

	let following: number | null = null;
	$effect(() => {
		if (!allowed || uid === following) return;
		const live = liveFor(TG_SERVICE);
		if (following !== null) live.stop();
		following = uid;
		live.start(userTopicFor(TG_SERVICE, uid));
	});
	onDestroy(() => {
		if (following !== null) liveFor(TG_SERVICE).stop();
	});
</script>

{#if allowed}
	{@render children()}
	{#if tgControl.copy}
		<ConfirmDialog
			bind:open={tgControl.open}
			title={tgControl.copy.title}
			body={tgControl.copy.body}
			confirmLabel={tgControl.copy.label}
			danger={tgControl.pending?.action === 'stop'}
			busy={tgControl.busy}
			onconfirm={() => tgControl.confirm()}
			oncancel={() => tgControl.cancel()}
		/>
	{/if}
{:else}
	<section class="mx-auto max-w-lg rounded-lg border border-line bg-surface p-6">
		<h1 class="text-lg font-semibold">{FORBIDDEN}</h1>
		<p class="mt-2 text-sm text-muted">You can only open your own bot's pages.</p>
	</section>
{/if}
