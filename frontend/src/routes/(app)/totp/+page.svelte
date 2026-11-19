<script lang="ts">
	import { ApiError, unwrap } from '#lib/api/client';
	import { api, loadMe, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Field from '#ui/Field';
	import Notice from '#ui/Notice';

	let enrolment: { secret: string; otpauth_uri: string } | null = $state(null);
	let code = $state('');
	let recovery: string[] = $state([]);
	let error: string | null = $state(null);
	let busy = $state(false);

	async function step(action: () => Promise<void>) {
		busy = true;
		error = null;
		try {
			await action();
		} catch (e) {
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			busy = false;
		}
	}

	const start = () =>
		step(async () => {
			enrolment = await unwrap(api.POST('/auth/totp/enrol'));
		});

	function verify(event: SubmitEvent) {
		event.preventDefault();
		void step(async () => {
			const result = await unwrap(api.POST('/auth/totp/verify', { body: { code: code.trim() } }));
			recovery = result.recovery_codes;
			enrolment = null;
			await loadMe();
		});
	}
</script>

<svelte:head><title>Two-factor sign-in · Casino dashboard</title></svelte:head>

<section class="mx-auto flex max-w-lg flex-col gap-4 rounded-lg border border-line bg-surface p-6">
	<h1 class="text-xl font-semibold">Two-factor sign-in</h1>
	{#if error}<Notice tone="error">{error}</Notice>{/if}
	{#if recovery.length}
		<Notice tone="success">Two-factor sign-in is on.</Notice>
		<p class="text-sm">
			Save these recovery codes somewhere safe. Each works once if you lose your phone. They are
			shown only now.
		</p>
		<ul class="grid grid-cols-2 gap-2 font-mono text-sm">
			{#each recovery as item (item)}<li>{item}</li>{/each}
		</ul>
	{:else if session.me?.totp_enabled}
		<Notice tone="success">Two-factor sign-in is on for {session.me.email}.</Notice>
	{:else if enrolment}
		<p class="text-sm">
			Add this key to your authenticator app, then enter the code it shows. Most apps can also open
			the setup link on this device.
		</p>
		<p class="font-mono text-sm break-all">{enrolment.secret}</p>
		<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an otpauth: link for the authenticator app, not a route -->
		<a href={enrolment.otpauth_uri} class="text-sm text-accent underline">Open in authenticator</a>
		<form onsubmit={verify} class="flex flex-col gap-4">
			<Field
				label="Authenticator code"
				inputmode="numeric"
				autocomplete="one-time-code"
				bind:value={code}
				required
			/>
			<Button type="submit" variant="primary" disabled={busy}>Turn on</Button>
		</form>
	{:else}
		<p class="text-sm text-muted">
			Owners and admins need it before they can control the bot or change settings.
		</p>
		<Button variant="primary" onclick={start} disabled={busy}>Set up two-factor sign-in</Button>
	{/if}
</section>
