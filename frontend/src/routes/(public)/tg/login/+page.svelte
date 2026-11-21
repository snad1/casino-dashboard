<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api, loadMe } from '#lib/session.svelte';
	import { tokenFromHash } from '#lib/token';
	import Notice from '#ui/Notice';

	const SIGN_IN_FAILED = "Couldn't sign you in. Send /web to the bot again.";
	const INCOMPLETE = 'This link is incomplete. Send /web to the bot again.';

	let error: string | null = $state(null);

	onMount(async () => {
		const token = tokenFromHash();
		if (token === null) {
			error = INCOMPLETE;
			return;
		}
		try {
			await unwrap(api.POST('/auth/telegram', { body: { token } }));
			const me = await loadMe();
			if (me?.tg_uid == null) throw new Error('not a Telegram account');
			await goto(resolve('/(app)/tg/[uid=uid]', { uid: String(me.tg_uid) }), {
				replaceState: true
			});
		} catch (e) {
			error = e instanceof ApiError && e.detail ? e.detail : SIGN_IN_FAILED;
		}
	});
</script>

<svelte:head><title>Signing in · Casino dashboard</title></svelte:head>

<h1 class="mb-4 text-xl font-semibold">Signing you in</h1>
{#if error}
	<Notice tone="error">{error}</Notice>
	<p class="mt-4 text-sm text-muted">
		Each link from the bot works once, for five minutes. Send /web to the bot for a new one.
	</p>
{:else}
	<p class="text-sm text-muted" role="status">Checking your link from the bot.</p>
{/if}
