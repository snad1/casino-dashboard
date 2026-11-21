<script lang="ts">
	import { page } from '$app/state';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api } from '#lib/session.svelte';
	import { settingLabel, settingValue } from '#lib/tg';
	import Notice from '#ui/Notice';

	const LOAD_FAILED = "Couldn't read your settings from the bot. Try again.";
	const uid = $derived(Number(page.params.uid));

	let config = $state<Record<string, unknown> | null>(null);
	let error = $state<string | null>(null);

	$effect(() => {
		const user = uid;
		config = null;
		error = null;
		unwrap(api.GET('/tg/{uid}/config', { params: { path: { uid: user } } }))
			.then((out) => (config = out.config))
			.catch((e) => (error = e instanceof ApiError && e.detail ? e.detail : LOAD_FAILED));
	});
</script>

<svelte:head><title>Settings · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-3xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Settings</h1>
	<p class="text-sm text-muted">
		Your bot's saved settings. Change them in Telegram with /set. The API key and proxy are never
		shown here.
	</p>
	{#if error}
		<Notice tone="error">{error}</Notice>
	{:else if config === null}
		<p class="text-sm text-muted" role="status">Asking the bot.</p>
	{:else if Object.keys(config).length === 0}
		<p class="text-sm text-muted">No settings are saved yet.</p>
	{:else}
		<dl
			class="grid grid-cols-1 gap-x-6 gap-y-2 rounded-lg border border-line bg-surface p-4 text-sm sm:grid-cols-2"
		>
			{#each Object.entries(config) as [key, value] (key)}
				<div>
					<dt class="text-muted">{settingLabel(key)}</dt>
					<dd class="font-mono break-words">{settingValue(value)}</dd>
				</div>
			{/each}
		</dl>
	{/if}
</div>
