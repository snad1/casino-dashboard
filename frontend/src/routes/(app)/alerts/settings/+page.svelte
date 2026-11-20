<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Channels } from '#lib/alerts';
	import { formatTime } from '#lib/analytics';
	import { ApiError, unwrap } from '#lib/api/client';
	import { deviceSubscribed, pushSupport, subscribe, unsubscribe, type PushApi } from '#lib/push';
	import { api, session } from '#lib/session.svelte';
	import { toast } from '#lib/toast.svelte';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';

	let channels = $state<Channels | null>(null);
	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let email = $state(false);
	let telegram = $state(false);
	let quietOn = $state(false);
	let quietStart = $state('23:00');
	let quietEnd = $state('07:00');
	let link = $state<{ url: string; expires_at: string } | null>(null);
	let linkError = $state<string | null>(null);
	let saving = $state(false);
	let pushPrivate = $state(true);
	let pushOn = $state(false);
	let pushBusy = $state(false);
	let pushError = $state<string | null>(null);
	const support = typeof window === 'undefined' ? 'unsupported' : pushSupport();

	const pushApi: PushApi = {
		add: async (body) => {
			await unwrap(api.POST('/alerts/me/push-subscriptions', { body }));
		},
		remove: async (endpoint) => {
			await unwrap(api.POST('/alerts/me/push-subscriptions/remove', { body: { endpoint } }));
		}
	};

	function message(e: unknown): string {
		return e instanceof ApiError ? e.detail : String(e);
	}

	function show(out: Channels) {
		channels = out;
		email = out.email_enabled;
		telegram = out.telegram_enabled;
		pushPrivate = out.push_private;
		quietOn = out.quiet_start !== null && out.quiet_end !== null;
		if (out.quiet_start) quietStart = out.quiet_start.slice(0, 5);
		if (out.quiet_end) quietEnd = out.quiet_end.slice(0, 5);
	}

	async function load() {
		try {
			show(await unwrap(api.GET('/alerts/me/channels')));
			loadError = null;
		} catch (e) {
			loadError = message(e);
		}
	}

	$effect(() => {
		if (session.me) void load();
	});

	$effect(() => {
		if (support === 'ready') void deviceSubscribed().then((on) => (pushOn = on));
	});

	async function togglePush() {
		if (!channels?.push_key) return;
		pushBusy = true;
		pushError = null;
		try {
			if (pushOn) {
				await unsubscribe(pushApi);
				pushOn = false;
				toast('success', 'Push notifications are off on this device.');
			} else if (await subscribe(channels.push_key, pushApi)) {
				pushOn = true;
				toast('success', 'Push notifications are on for this device.');
			} else {
				pushError = 'Notifications were not allowed, so push stays off on this device.';
			}
		} catch (e) {
			pushError = message(e);
		} finally {
			pushBusy = false;
		}
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		saving = true;
		saveError = null;
		try {
			show(
				await unwrap(
					api.PUT('/alerts/me/channels', {
						body: {
							email_enabled: email,
							telegram_enabled: telegram,
							push_private: pushPrivate,
							quiet_start: quietOn ? `${quietStart}:00` : null,
							quiet_end: quietOn ? `${quietEnd}:00` : null
						}
					})
				)
			);
			toast('success', 'Alert settings saved.');
		} catch (e) {
			saveError = message(e);
		} finally {
			saving = false;
		}
	}

	async function makeLink() {
		linkError = null;
		try {
			link = await unwrap(api.POST('/alerts/me/telegram-link'));
		} catch (e) {
			linkError = message(e);
		}
	}

	const input = 'min-h-11 rounded-md border border-line-strong bg-surface px-3';
	const check = 'flex min-h-11 items-center gap-2 text-sm font-medium';
</script>

<svelte:head><title>My alert settings · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-2xl flex-col gap-4">
	<h1 class="text-xl font-semibold">My alert settings</h1>
	<p class="text-sm text-muted">
		Alerts from the <a class="text-accent underline" href={resolve('/(app)/alerts')}>alert rules</a> reach
		you here. Quiet hours hold alerts until they end; nothing is lost.
	</p>

	{#if loadError}
		<Notice tone="error">{loadError}</Notice>
	{:else if channels === null}
		<p class="text-sm text-muted" aria-live="polite">Loading your settings...</p>
	{:else}
		<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="tg-title">
			<h2 id="tg-title" class="font-semibold">Telegram</h2>
			{#if !channels.telegram_available}
				<p class="mt-1 text-sm text-muted">Telegram alerts are not set up on this dashboard.</p>
			{:else if channels.telegram_linked}
				<p class="mt-1 text-sm">Linked. You can turn Telegram alerts on or off below.</p>
			{:else}
				<p class="mt-1 text-sm text-muted">Not linked yet.</p>
			{/if}
			{#if channels.telegram_available}
				<div class="mt-3 flex flex-wrap items-center gap-3">
					<Button onclick={makeLink}
						>{channels.telegram_linked ? 'Link another chat' : 'Link Telegram'}</Button
					>
					{#if link}
						<!-- eslint-disable svelte/no-navigation-without-resolve -- the alert bot on Telegram, not an app route -->
						<a
							class="inline-flex min-h-11 items-center rounded-md bg-accent px-4 text-sm font-medium text-on-accent"
							href={link.url}
							target="_blank"
							rel="noopener noreferrer">Open the alert bot</a
						>
						<!-- eslint-enable svelte/no-navigation-without-resolve -->
						<p class="text-sm text-muted">
							Press Start in Telegram before {formatTime(link.expires_at).slice(11, 16)} UTC, then reload
							this page.
						</p>
					{/if}
				</div>
				{#if linkError}
					<div class="mt-3"><Notice tone="error">{linkError}</Notice></div>
				{/if}
			{/if}
		</section>

		<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="push-title">
			<h2 id="push-title" class="font-semibold">Push notifications</h2>
			{#if !channels.push_key}
				<p class="mt-1 text-sm text-muted">Push notifications are not set up on this dashboard.</p>
			{:else if support === 'unsupported'}
				<p class="mt-1 text-sm text-muted">Push notifications are not available in this browser.</p>
			{:else if support === 'blocked'}
				<p class="mt-1 text-sm text-muted">
					Notifications are blocked for this site. Allow them in the browser settings.
				</p>
			{:else}
				<p class="mt-1 text-sm">{pushOn ? 'On for this device.' : 'Off for this device.'}</p>
				<div class="mt-3">
					<Button onclick={togglePush} disabled={pushBusy}>
						{pushOn ? 'Turn off push here' : 'Turn on push here'}
					</Button>
				</div>
				{#if pushError}
					<div class="mt-3"><Notice tone="error">{pushError}</Notice></div>
				{/if}
			{/if}
		</section>

		<form onsubmit={save} class="flex flex-col gap-4 rounded-lg border border-line bg-surface p-4">
			<h2 class="font-semibold">Channels and quiet hours</h2>
			<label class={check}>
				<input type="checkbox" class="size-5" bind:checked={email} />
				Email alerts to {session.me?.email}
			</label>
			<label class={check}>
				<input
					type="checkbox"
					class="size-5"
					bind:checked={telegram}
					disabled={!channels.telegram_linked}
				/>
				Telegram alerts{channels.telegram_linked ? '' : ' (link Telegram first)'}
			</label>
			<label class={check}>
				<input type="checkbox" class="size-5" bind:checked={pushPrivate} />
				Private notifications (hide alert text)
			</label>
			<label class={check}>
				<input type="checkbox" class="size-5" bind:checked={quietOn} />
				Quiet hours
			</label>
			{#if quietOn}
				<div class="flex flex-wrap gap-3">
					<label class="flex flex-col gap-1 text-sm font-medium">
						From
						<input type="time" class={input} bind:value={quietStart} required />
					</label>
					<label class="flex flex-col gap-1 text-sm font-medium">
						Until
						<input type="time" class={input} bind:value={quietEnd} required />
					</label>
				</div>
				<p class="text-sm text-muted">Times are in the bot host's time zone.</p>
			{/if}
			{#if saveError}
				<Notice tone="error">{saveError}</Notice>
			{/if}
			<div>
				<Button variant="primary" type="submit" disabled={saving}>Save</Button>
			</div>
		</form>
	{/if}
</div>
